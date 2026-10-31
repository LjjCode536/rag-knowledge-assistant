"""运营统计与成本统计基准(T16):统计口径与成本折算的可复现前后对比。

AGENTS.md 的"评估驱动迭代"约定下,每个迭代要产出可复现的前后指标对比。
T16(M7·收口)的可控指标不是检索质量(问答链路与 T15 相同),而是**统计与
成本口径本身**:统计能否一屏回答"系统健康吗、花了多少",以及成本折算是否
与配置单价一致。

本脚本在真实 uvicorn 服务(临时端口)上跑,全程进程内、不触任何真实上游、
零计费:

1. **形态 A(before)**:没有统计面 —— 只能逐条翻查询日志,靠人肉数;
2. **形态 B(after)**:``GET /api/v1/stats`` 一屏给全核心指标,``/cost`` 给
   成本明细;并对比"区间查询"与"全量查询"两种口径。

输出:核心指标表 + 统计端点延迟 + 成本折算的逐项核对(独立手算,不复用
被测代码的折算逻辑)。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.domain.qa import GenerationUsage, GeneratorKind
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.services.generation import GeneratedAnswer

CORPUS_SECTIONS = "".join(
    f"### 第 {chapter} 章 制度细则\n\n员工年假需提前 {chapter} 天申请,审批周期两个工作日。\n"
    for chapter in range(1, 21)
)
# 规则版生成器命中"年假 + 入职 + 一年"才给出可回答的答案,语料里要有这一句
CORPUS_MD = (
    f"# 员工手册\n\n## 休假制度\n\n员工入职满一年后,可以按照公司制度申请年假。\n\n"
    f"{CORPUS_SECTIONS}"
)

ANSWERABLE_QUESTION = "入职多久可以申请年假?"
UNANSWERABLE_QUESTION = "公司食堂的菜单在哪里查看?"

# 基准用的固定 Token 口径(与折算公式独立:脚本手算一遍,与端点返回值核对)
PROMPT_TOKENS = 1200
COMPLETION_TOKENS = 300
PROMPT_PRICE_PER_1K = 0.002
COMPLETION_PRICE_PER_1K = 0.006


class _TokenGenerator:
    """带固定 Token 用量的生成器替身:让成本折算在离线环境可核对。

    规则版生成器恒零 Token,测不出折算公式;这里按固定口径回报用量,
    端点返回的金额应与脚本手算一致。
    """

    kind = GeneratorKind.LLM

    async def generate(self, question, *, context):
        return GeneratedAnswer(
            text="根据知识库资料,员工入职满一年后,可以按照公司制度申请年假。",
            usage=GenerationUsage(
                prompt_tokens=PROMPT_TOKENS,
                completion_tokens=COMPLETION_TOKENS,
                total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
            ),
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


def _settings() -> Settings:
    """基准配置:固定 Token 用量的生成器替身、同步入库、无密钥环境不降级。

    答档缓存**关闭**:本基准要测的是"每次问答都真生成"时的成本口径;
    缓存命中记零 Token(ADR-0013),开着缓存会让手算口径对不上。
    """
    return Settings(
        _env_file=None,
        environment="test",
        qa_generator="rule",
        ingestion_async=False,
        use_query_rewrite=False,
        use_rerank=False,
        qa_cache_enabled=False,
        log_level="WARNING",
        cost_prompt_price_per_1k=PROMPT_PRICE_PER_1K,
        cost_completion_price_per_1k=COMPLETION_PRICE_PER_1K,
    )


def _ingest_corpus(client: httpx.Client, base_url: str) -> str:
    """上传 → 切片 → 向量化,返回文档 id。"""
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
    return document_id


def _ask(client: httpx.Client, base_url: str, question: str) -> dict[str, Any]:
    response = client.post(
        f"{base_url}/api/v1/chat", json={"question": question}, timeout=60.0
    )
    response.raise_for_status()
    return response.json()["data"]


def _timed_get(client: httpx.Client, url: str) -> tuple[float, dict[str, Any]]:
    """一次 GET 的端到端延迟(毫秒)与响应体。"""
    started = time.perf_counter()
    response = client.get(url, timeout=60.0)
    latency = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return latency, response.json()["data"]


def main() -> None:
    parser = argparse.ArgumentParser(description="运营统计与成本统计基准(T16)")
    parser.add_argument(
        "--questions",
        type=int,
        default=40,
        help="可回答问题重复问多少次(其余为不可回答的拒答问)(默认 40)",
    )
    args = parser.parse_args()

    settings = _settings()
    with tempfile.TemporaryDirectory(prefix="rag-benchmark-stats-") as temp_dir:
        configured = settings.model_copy(update={"upload_dir": Path(temp_dir)})
        # 缓存开启形态:单独一份应用(缓存是进程内状态,不能与关闭形态共用)
        cached_app = create_app(
            configured.model_copy(update={"qa_cache_enabled": True}),
            embedder=DeterministicEmbedder(),
            answer_generator=_TokenGenerator(),
        )
        app = create_app(
            configured,
            embedder=DeterministicEmbedder(),
            answer_generator=_TokenGenerator(),
        )
        with _Server(app) as base_url, httpx.Client() as client:
            _ingest_corpus(client, base_url)

            # 制造问答流量:可回答 + 不可回答(拒答),两类都留查询日志
            for _ in range(args.questions):
                _ask(client, base_url, ANSWERABLE_QUESTION)
            for _ in range(args.questions // 2):
                _ask(client, base_url, UNANSWERABLE_QUESTION)

            # 形态 B:统计端点
            stats_latencies: list[float] = []
            stats_data: dict[str, Any] = {}
            for _ in range(20):
                latency, stats_data = _timed_get(client, f"{base_url}/api/v1/stats")
                stats_latencies.append(latency)

            cost_latency, _cost_data = _timed_get(
                client, f"{base_url}/api/v1/stats/cost"
            )

            # 区间查询:只取最近一小时(ISO 8601 的 Z 形式:URL 里 + 会被当空格)
            now = datetime.now(UTC)
            _, ranged = _timed_get(
                client,
                f"{base_url}/api/v1/stats"
                f"?start={(now - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}"
                f"&end={(now + timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}"
                f"&bucket=hour",
            )

            # 形态 A(before):逐条翻查询日志(只能拿到单条记录,聚合靠人肉)
            logs_latency, logs_data = _timed_get(
                client, f"{base_url}/api/v1/query-logs?limit=100"
            )

        # 缓存开启形态:同题重复问,只有首次真正生成
        with _Server(cached_app) as cached_url, httpx.Client() as cached_client:
            _ingest_corpus(cached_client, cached_url)
            for _ in range(args.questions):
                _ask(cached_client, cached_url, ANSWERABLE_QUESTION)
            for _ in range(args.questions // 2):
                _ask(cached_client, cached_url, UNANSWERABLE_QUESTION)
            _, cached = _timed_get(cached_client, f"{cached_url}/api/v1/stats")

    # ===== 输出 =====
    qa = stats_data["qa"]
    cost = stats_data["cost"]
    answered = qa["query_count"] - qa["refused_count"]
    cached_answered = cached["qa"]["query_count"] - cached["qa"]["refused_count"]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")

    print(f"\n## 运营统计与成本统计(T16,{stamp})\n")
    print("### 语料与流量\n")
    print(f"- 文档:1 份员工手册(20 章,切片后入库);问答:{args.questions} 次可回答"
          f" + {args.questions // 2} 次不可回答(拒答)")
    print(f"- 生成器:固定 Token 用量的替身(每次 {PROMPT_TOKENS} prompt / "
          f"{COMPLETION_TOKENS} completion),用于离线核对折算公式\n")

    print("### 一屏核心指标(GET /api/v1/stats)\n")
    print("| 指标 | 值 |")
    print("| --- | ---: |")
    print(f"| 文档数 | {stats_data['document_count']} |")
    print(f"| 已索引文档数 | {stats_data['indexed_document_count']} |")
    print(f"| 切片数 | {stats_data['chunk_count']} |")
    print(f"| 问答量 | {qa['query_count']} |")
    print(f"| 拒答数 | {qa['refused_count']} |")
    print(f"| 拒答率 | {qa['refusal_rate']:.4f} |")
    print(f"| 平均延迟 | {qa['avg_latency_ms']:.2f} ms |")
    print(f"| 分桶数 | {len(stats_data['buckets'])} |")
    print()

    print("### 成本折算核对(GET /api/v1/stats/cost)\n")
    print("| 项 | 端点返回 | 手算(独立公式) | 一致 |")
    print("| --- | ---: | ---: | :---: |")
    # 手算独立于被测代码:每次问答固定用量,乘以问答量,再按单价折算
    expected_prompt = answered * PROMPT_TOKENS / 1000 * PROMPT_PRICE_PER_1K
    expected_completion = answered * COMPLETION_TOKENS / 1000 * COMPLETION_PRICE_PER_1K
    expected_total = expected_prompt + expected_completion
    print(
        f"| prompt 成本 | {cost['prompt_cost']:.6f} | {expected_prompt:.6f} "
        f"| {'✓' if abs(cost['prompt_cost'] - expected_prompt) < 1e-6 else '✗'} |"
    )
    print(
        f"| completion 成本 | {cost['completion_cost']:.6f} | {expected_completion:.6f} "
        f"| {'✓' if abs(cost['completion_cost'] - expected_completion) < 1e-6 else '✗'} |"
    )
    print(
        f"| 总成本 | {cost['total_cost']:.6f} | {expected_total:.6f} "
        f"| {'✓' if abs(cost['total_cost'] - expected_total) < 1e-6 else '✗'} |"
    )
    print()
    print(
        f"注:Token 用量来自生成器替身的固定口径(每次 {PROMPT_TOKENS} prompt / "
        f"{COMPLETION_TOKENS} completion),只有**可回答**的 {answered} 次问答真正生成;"
        f"手算列独立按 单价 {PROMPT_PRICE_PER_1K} / {COMPLETION_PRICE_PER_1K} "
        f"元每千 Token 折算。\n"
    )

    print("### 读统计的代价(端点延迟)\n")
    print("| 形态 | 平均 | 中位 | 最快 | 最慢 |")
    print("| --- | ---: | ---: | ---: | ---: |")
    print(
        f"| 聚合统计 /stats | {statistics.mean(stats_latencies):.2f} ms"
        f" | {statistics.median(stats_latencies):.2f} ms"
        f" | {min(stats_latencies):.2f} ms | {max(stats_latencies):.2f} ms |"
    )
    print(
        f"| 逐条日志 /query-logs(100 条) | {logs_latency:.2f} ms | — | — | — |"
    )
    print(
        f"| 成本明细 /stats/cost | {cost_latency:.2f} ms | — | — | — |"
    )
    print()

    print("### 区间口径\n")
    print(f"- 全量(不设界):问答量 {qa['query_count']},拒答率 {qa['refusal_rate']:.4f}")
    print(
        f"- 最近一小时:问答量 {ranged['qa']['query_count']},"
        f"拒答率 {ranged['qa']['refusal_rate']}"
    )
    print(
        f"- 形态 A(before)的查询日志列表只给到 {logs_data['total']} 条单条记录,"
        f"聚合口径(拒答率 / 平均延迟 / Token 合计)需调用方自行在应用侧汇总"
    )
    print()

    print("### 答档缓存命中不计成本(ADR-0013 / ADR-0014)\n")
    print("| 形态 | 问答量 | 计费生成次数 | 估算总成本 |")
    print("| --- | ---: | ---: | ---: |")
    print(
        f"| 缓存关闭(上表口径) | {qa['query_count']} | {answered} | "
        f"{cost['total_cost']:.6f} |"
    )
    print(
        f"| 缓存开启(同题问 {args.questions} 次) | {cached['qa']['query_count']} | "
        f"{cached_answered} | {cached['cost']['total_cost']:.6f} |"
    )
    print()
    print(
        "缓存命中时生成链路整段跳过(零上游消耗),查询日志记零 Token —— "
        "因此成本只算真正发生的生成,不把首发请求的用量重复计入每次命中。"
    )


if __name__ == "__main__":
    main()
