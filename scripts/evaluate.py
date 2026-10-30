"""黄金数据集评估器:一条命令经 HTTP 主缝跑出检索指标并落报告。

默认在进程内启动应用并注入确定性向量替身,因此无密钥环境(CI)也能跑;
连接真实服务时传入 ``--base-url``,报告中会注明向量来源,便于区分基线口径。

用法::

    uv run python scripts/evaluate.py
    uv run python scripts/evaluate.py --iteration 0002 --slug t07-advanced-retrieval \\
        --run-name "T07 高级检索" --base-url http://127.0.0.1:8000 \\
        --min-score 0.35 --embedder-label BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.domain.chunks import ChunkingStrategy
from app.domain.evaluation import EvaluationReport
from app.integrations.llm.deterministic import EVALUATION_DIM, DeterministicEmbedder
from app.main import create_app
from app.services.evaluation import (
    DEFAULT_EMBEDDER_LABEL,
    DEFAULT_RUN_NAME,
    EvaluationApiError,
    EvaluationConfig,
    EvaluationDatasetError,
    EvaluationService,
    load_golden_dataset,
    render_json,
    render_markdown,
)

DEFAULT_DATASET = Path("evaluation/golden_dataset.yaml")
DEFAULT_REPORT_DIR = Path("docs/iterations")
DEFAULT_ITERATION = "0001"
DEFAULT_SLUG = "t06-retrieval-baseline"
_ITERATION_PATTERN = re.compile(r"^\d{4}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="跑黄金数据集检索指标,输出 Markdown + JSON 报告。",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--run-name",
        default=DEFAULT_RUN_NAME,
        help=f"报告标题中的运行名称(默认 {DEFAULT_RUN_NAME})",
    )
    parser.add_argument(
        "--iteration",
        default=DEFAULT_ITERATION,
        help="报告编号(4 位数字);后续迭代递增,如 0002",
    )
    parser.add_argument(
        "--slug",
        default=DEFAULT_SLUG,
        help=f"报告文件名 slug(默认 {DEFAULT_SLUG})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Markdown 报告路径;缺省为 docs/iterations/<iteration>-<slug>.md",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="JSON 报告路径;缺省与 Markdown 同名 .json",
    )
    parser.add_argument("--k", type=int, default=3, help="Recall@K / NDCG@K 的 K(默认 3)")
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="实际检索条数;缺省等于 K。T07 消融可先召回更多候选再按 K 评估",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="拒答阈值;缺省用应用配置 QA_MIN_SCORE(默认 0.35)",
    )
    parser.add_argument(
        "--strategy",
        choices=[strategy.value for strategy in ChunkingStrategy],
        default=None,
        help="切分策略;缺省用应用配置 CHUNK_STRATEGY",
    )
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--chunk-overlap", type=int, default=None)
    parser.add_argument(
        "--embedder",
        choices=("deterministic", "upstream"),
        default="deterministic",
        help="进程内模式的向量来源;deterministic 不需要密钥(默认)",
    )
    parser.add_argument(
        "--embedder-label",
        default=None,
        help="外部模式(--base-url)记录到报告里的向量来源,如 BAAI/bge-m3",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="改为评估已在运行的服务(如 http://127.0.0.1:8000),不再进程内启动",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="保留应用 INFO 结构化日志;默认只打印结果与告警",
    )
    return parser.parse_args(argv)


async def _run(
    args: argparse.Namespace, dataset, config: EvaluationConfig
) -> EvaluationReport:
    if args.base_url:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=120.0) as client:
            service = EvaluationService(
                client=client,
                dataset=dataset,
                dataset_dir=args.dataset.resolve().parent,
                config=config,
            )
            return await service.run()

    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="rag-evaluation-") as temp_dir:
        updates = {"upload_dir": Path(temp_dir)}
        if not args.verbose:
            updates.update({"log_level": "WARNING", "log_format": "console"})
        settings = settings.model_copy(update=updates)
        embedder = None
        if args.embedder == "deterministic":
            embedder = DeterministicEmbedder(dim=EVALUATION_DIM)
        app = create_app(settings, embedder=embedder)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://evaluation", timeout=120.0
            ) as client:
                service = EvaluationService(
                    client=client,
                    dataset=dataset,
                    dataset_dir=args.dataset.resolve().parent,
                    config=config,
                )
                return await service.run()
        finally:
            await app.state.llm_client.aclose()


def main(argv: list[str] | None = None) -> int:
    raw_args = argv if argv is not None else sys.argv[1:]
    args = _parse_args(argv)
    if args.k < 1:
        print("--k 必须大于 0", file=sys.stderr)
        return 2
    if args.top_k is not None and not (args.k <= args.top_k <= 50):
        print("--top-k 必须介于 --k 与 50 之间", file=sys.stderr)
        return 2
    if not _ITERATION_PATTERN.fullmatch(args.iteration):
        print("--iteration 必须是 4 位数字,如 0001", file=sys.stderr)
        return 2
    if not _SLUG_PATTERN.fullmatch(args.slug):
        print("--slug 只能包含小写字母、数字和连字符", file=sys.stderr)
        return 2
    if args.base_url and args.min_score is None:
        print("外部模式必须显式提供 --min-score(客户端无法读取服务端配置)", file=sys.stderr)
        return 2
    if args.base_url and not args.embedder_label:
        print(
            "外部模式必须显式提供 --embedder-label,如 --embedder-label BAAI/bge-m3",
            file=sys.stderr,
        )
        return 2
    if (
        args.run_name != DEFAULT_RUN_NAME
        and args.report is None
        and args.json_report is None
        and args.iteration == DEFAULT_ITERATION
        and args.slug == DEFAULT_SLUG
    ):
        print(
            "自定义 --run-name 时请同时指定 --iteration 与 --slug(或显式 --report),"
            "避免覆盖 T06 基线",
            file=sys.stderr,
        )
        return 2

    report_path = args.report or DEFAULT_REPORT_DIR / f"{args.iteration}-{args.slug}.md"
    json_report_path = args.json_report or report_path.with_suffix(".json")

    dataset_path = args.dataset.resolve()
    try:
        dataset = load_golden_dataset(dataset_path)
    except Exception as exc:  # 数据集文件缺失 / YAML / 字段校验失败
        print(f"读取黄金数据集失败:{exc}", file=sys.stderr)
        return 1

    settings = get_settings()
    min_score = args.min_score if args.min_score is not None else settings.qa_min_score
    mode = "external" if args.base_url else "in-process"
    if args.base_url:
        embedder = args.embedder_label
    elif args.embedder == "deterministic":
        embedder = DEFAULT_EMBEDDER_LABEL
    else:
        embedder = settings.embedding_model
    assert embedder is not None  # 外部模式已在上面校验
    command = "uv run python scripts/evaluate.py " + " ".join(raw_args)
    config = EvaluationConfig(
        k=args.k,
        top_k=args.top_k,
        min_score=min_score,
        chunk_strategy=ChunkingStrategy(args.strategy) if args.strategy else None,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        embedder=embedder,
        mode=mode,
        command=command.strip(),
        run_name=args.run_name,
    )

    try:
        report = asyncio.run(_run(args, dataset, config))
    except (EvaluationApiError, EvaluationDatasetError) as exc:
        print(f"评估失败:{exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    json_text = render_json(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8", newline="\n")
    json_report_path.parent.mkdir(parents=True, exist_ok=True)
    json_report_path.write_text(json_text, encoding="utf-8", newline="\n")

    print(
        f"用例 {report.cases_total} 条 | "
        f"Recall@{report.k}={report.summary.recall_at_k:.4f} | "
        f"MRR={report.summary.mrr:.4f} | "
        f"NDCG@{report.k}={report.summary.ndcg_at_k:.4f} | "
        f"拒答={report.refusal_accuracy if report.refusal_accuracy is not None else '—'} | "
        f"权限={report.permission_pass_rate if report.permission_pass_rate is not None else '—'}"
    )
    print(f"报告:{report_path}")
    print(f"JSON:{json_report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
