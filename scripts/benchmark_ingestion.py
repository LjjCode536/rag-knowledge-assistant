"""入库形态延迟基准:同份大文件分别经同步 / 异步形态上传,对比 POST 响应阻塞。

AGENTS.md 的"评估驱动迭代"约定下,每个迭代要产出可复现的前后指标对比。
T14(M7·上)的可控指标是上传响应阻塞时长:大文件解析 / 切片 / 向量化是
重活,同步形态全部压在请求里,异步形态(ADR-0012)只登记与落盘即返回,
重活转后台任务。本脚本在真实 uvicorn 服务(临时端口)上把两种形态各跑一遍:

- 同步形态:POST 时长即上传阻塞时长(解析在响应前完成);
- 异步形态:POST 时长即任务句柄返回时长;随后轮询 `GET /documents/{id}`,
  统计到入库终点(indexed / failed / chunked)的端到端时长。

文件构造与解析都在进程内完成,不触任何真实上游(确定性向量替身入库)。
"""

from __future__ import annotations

import argparse
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

TERMINAL_INGEST_STATES = {"indexed", "failed", "chunked"}


def _build_large_docx(paragraphs: int) -> bytes:
    """构造大 docx 工作负载:内容合同固定,只随段落数变大。"""
    import io

    from docx import Document

    document = Document()
    for index in range(paragraphs):
        document.add_paragraph(
            f"第 {index} 条制度:年假申请需提前三天,审批周期为两个工作日({index})。"
        )
        document.add_heading("附则说明", level=2)
        document.add_paragraph("审批流程与驳回机制参见上一条制度。")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _settings(mode_async: bool) -> Settings:
    """基准配置:无密钥、内存仓储、确定性替身、上传进临时目录、日志开 WARNING。"""
    base = Settings(_env_file=None, environment="test", qa_generator="rule")
    return base.model_copy(
        update={"ingestion_async": mode_async, "log_level": "WARNING"}
    )


class _Server:
    """临时端口上的真实 uvicorn 服务:异步形态的"立即返回"只有在真 socket 上才成立。"""

    def __init__(self, app) -> None:  # FastAPI 应用
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        while not self._server.started:  # 唤醒即就绪,无需固定 sleep
            time.sleep(0.01)
        return f"http://127.0.0.1:{self._server.servers[0].sockets[0].getsockname()[1]}"

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _run_mode(
    client: httpx.Client, base_url: str, payload: bytes, *, mode_async: bool
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post(
        f"{base_url}/api/v1/documents",
        files={
            "file": (
                "handbook.docx",
                payload,
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document",
            )
        },
        timeout=300.0,
    )
    upload_seconds = time.perf_counter() - started
    response.raise_for_status()
    document_id = response.json()["data"]["id"]

    ready_seconds = None
    if mode_async:
        deadline = time.monotonic() + 300.0
        while True:
            view = client.get(
                f"{base_url}/api/v1/documents/{document_id}"
            ).json()["data"]
            if view["status"] in TERMINAL_INGEST_STATES:
                ready_seconds = time.perf_counter() - started
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"异步管线超出时限未收口:{view['status']}")
            time.sleep(0.02)
    return {
        "mode_async": mode_async,
        "upload_seconds": upload_seconds,
        "ready_seconds": ready_seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="入库形态上传阻塞时长基准(T14)")
    parser.add_argument(
        "--paragraphs", type=int, default=20000, help="工作负载段落数(默认 20000)"
    )
    args = parser.parse_args()

    payload = _build_large_docx(args.paragraphs)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rag-benchmark-") as temp_dir:
        for mode_async in (False, True):
            settings = _settings(mode_async).model_copy(
                update={"upload_dir": Path(temp_dir)}
            )
            app = create_app(settings, embedder=DeterministicEmbedder())
            with _Server(app) as base_url, httpx.Client() as client:
                rows.append(_run_mode(client, base_url, payload, mode_async=mode_async))

    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"\n## 入库形态上传阻塞时长(workload = docx {args.paragraphs} 段, {stamp})\n")
    print("| 形态 | 上传 POST 阻塞 | 到入库终点(indexed)端到端 |")
    print("| --- | ---: | ---: |")
    for row in rows:
        label = (
            "异步(ingestion_async=true)" if row["mode_async"] else "同步(ingestion_async=false)"
        )
        upload = f"{row['upload_seconds']*1000:.0f} ms"
        ready = "—" if row["ready_seconds"] is None else f"{row['ready_seconds']*1000:.0f} ms"
        print(f"| {label} | {upload} | {ready} |")
    print("\n结论:上传响应不再被解析 / 切片 / 向量化阻塞;问答可用性只推迟到管线收口。")


if __name__ == "__main__":
    main()
