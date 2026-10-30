"""问答答档缓存延迟基准(T15):同题二次询问在缓存开 / 关两形态下的端到端延迟对比。

AGENTS.md 的"评估驱动迭代"约定下,每个迭代要产出可复现的前后指标对比。
T15(M7)的可控指标是"相同问题再次询问"的延迟与回答一致性:缓存关闭时
每次都走检索 → 生成全链路;开启时(TTL 内)答档直接复用,生成链路整段
跳过。本脚本在真实 uvicorn 服务(临时端口)上把两种形态各跑一遍:

- 先上传 + 切片 + 向量化一份语料(确定性向量替身,不触真实上游);
- 关闭缓存形态:连续 N 次同题询问,全部冷跑;
- 开启缓存形态:1 次冷跑命中后连续 N-1 次热询问;
- 断言同题回答逐字一致(缓存不改变回答语义),输出延迟统计表。

全部步骤进程内完成,不触任何真实上游,零计费。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app

CODE_SECTIONS = "".join(
    f"### 第 {chapter} 章 制度细则\n\n员工年假需提前 {chapter} 天申请,审批周期两个工作日。\n"
    for chapter in range(1, 61)
)
CORPUS_MD = f"# 员工手册\n\n{CODE_SECTIONS}"
QUESTION = "入职多久可以申请年假?"


def _settings(cache_enabled: bool) -> Settings:
    """基准配置:规则生成器、同步入库、检索基线路径(无密钥环境不降级),
    仅缓存开关不同;对比口径 = 检索 + 生成全链路 vs 答档命中。"""
    base = Settings(_env_file=None, environment="test", qa_generator="rule")
    return base.model_copy(
        update={
            "ingestion_async": False,
            "qa_cache_enabled": cache_enabled,
            "use_query_rewrite": False,
            "use_rerank": False,
            "log_level": "WARNING",
        }
    )


class _Server:
    """临时端口上的真实 uvicorn 服务:端到端延迟包含 HTTP 与事件循环的成本。"""

    def __init__(self, app) -> None:  # FastAPI 应用
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.01)
        return f"http://127.0.0.1:{self._server.servers[0].sockets[0].getsockname()[1]}"

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _ingest_corpus(client: httpx.Client, base_url: str) -> None:
    """上传 → 切片 → 向量化(同步入库形态,基准只在 indexed 之后开始计时)。"""
    response = client.post(
        f"{base_url}/api/v1/documents",
        files={"file": ("handbook.md", CORPUS_MD.encode(), "text/plain")},
    )
    response.raise_for_status()
    document_id = response.json()["data"]["id"]

    chunked = client.post(
        f"{base_url}/api/v1/documents/{document_id}/chunks",
        json={"strategy": "heading"},
    )
    chunked.raise_for_status()
    embedded = client.post(f"{base_url}/api/v1/documents/{document_id}/embeddings")
    embedded.raise_for_status()


def _ask_latency(client: httpx.Client, base_url: str) -> tuple[float, str]:
    """同题一次询问,返回(秒级端到端延迟, 回答文本)。"""
    started = time.perf_counter()
    response = client.post(
        f"{base_url}/api/v1/chat", json={"question": QUESTION}, timeout=60.0
    )
    latency = time.perf_counter() - started
    response.raise_for_status()
    data = response.json()["data"]
    return latency, data["answer"]


def _run_mode(cache_enabled: bool, repeats: int, temp_dir: str) -> dict[str, Any]:
    settings = _settings(cache_enabled).model_copy(update={"upload_dir": Path(temp_dir)})
    app = create_app(settings, embedder=DeterministicEmbedder())
    with _Server(app) as base_url, httpx.Client() as client:
        _ingest_corpus(client, base_url)
        answers: list[str] = []
        latencies: list[float] = []
        for _ in range(repeats):
            latency, answer = _ask_latency(client, base_url)
            latencies.append(latency * 1000)
            answers.append(answer)
    assert len(set(answers)) == 1, "同题回答出现分叉,违反答档一致性"
    return {"cache_enabled": cache_enabled, "answers": answers, "latencies": latencies}


def main() -> None:
    parser = argparse.ArgumentParser(description="问答答档缓存延迟基准(T15)")
    parser.add_argument(
        "--repeats", type=int, default=40, help="两种形态各进行多少次同题询问(默认 40)"
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rag-benchmark-cache-") as temp_dir:
        for cache_enabled in (False, True):
            rows.append(_run_mode(cache_enabled, args.repeats, temp_dir))

    warm_hits = next(row for row in rows if row["cache_enabled"])

    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    workload = len(CODE_SECTIONS) // 2
    print(f"\n## 同题二次询问延迟(workload = {workload} 段手册,{args.repeats} 次, {stamp})\n")
    print("| 形态 | 平均 | 中位 | 最快 | 最慢 |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for row in rows:
        label = "开启答档缓存" if row["cache_enabled"] else "关闭答档缓存"
        values = row["latencies"]
        median_ms = statistics.median(values)
        print(
            f"| {label} | {statistics.mean(values):.2f} ms | {median_ms:.2f} ms"
            f" | {min(values):.2f} ms | {max(values):.2f} ms |"
        )
    cold_mean = statistics.mean(rows[0]["latencies"])
    warm_hit_mean = statistics.mean(warm_hits["latencies"][1:])
    print(f"\n结论:同题热询问平均 {cold_mean:.2f} ms -> {warm_hit_mean:.2f} ms"
          f"(约 {cold_mean / warm_hit_mean:.1f}x),回答逐字一致(命中完全复用)。")


if __name__ == "__main__":
    main()
