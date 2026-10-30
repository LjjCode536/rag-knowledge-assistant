"""黄金数据集评估器:一条命令经 HTTP 主缝跑出检索指标并落报告。

默认在进程内启动应用并注入确定性向量替身,因此无密钥环境(CI)也能跑;
连接真实服务时传入 ``--base-url``,报告中会注明向量来源,便于区分基线口径。

T07 起支持高级检索消融与开关:

- ``--ablate`` 在同一数据集上重跑「全关 / 混合 / 重排 / 混合+重排 / +MMR」
  组合矩阵,产出与 T06 基线的差值表;重排组合需要密钥,无密钥时注明跳过。
- ``--hybrid/--no-hybrid`` 等 BooleanOptional 开关覆盖应用默认组合。
- 各组合的检索上游调用全部单线程串行,不触免费档限速。

用法::

    uv run python scripts/evaluate.py
    uv run python scripts/evaluate.py --iteration 0002 --slug t07-advanced-retrieval \\
        --run-name "T07 高级检索" --base-url http://127.0.0.1:8000 \\
        --min-score 0.35 --embedder-label BAAI/bge-m3
    uv run python scripts/evaluate.py --iteration 0002 --slug t07-ablation \\
        --run-name "T07 高级检索消融" --ablate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings
from app.domain.chunks import ChunkingStrategy
from app.domain.evaluation import EvaluationReport, GenerationMetrics
from app.integrations.llm.deterministic import EVALUATION_DIM, DeterministicEmbedder
from app.main import create_app
from app.services.evaluation import (
    DEFAULT_EMBEDDER_LABEL,
    DEFAULT_RUN_NAME,
    RETRIEVAL_TOGGLE_FIELDS,
    EvaluationApiError,
    EvaluationConfig,
    EvaluationDatasetError,
    EvaluationService,
    load_golden_dataset,
    render_ablation_json,
    render_ablation_markdown,
    render_json,
    render_markdown,
)

DEFAULT_DATASET = Path("evaluation/golden_dataset.yaml")
DEFAULT_REPORT_DIR = Path("docs/iterations")
DEFAULT_ITERATION = "0001"
DEFAULT_SLUG = "t06-retrieval-baseline"
DEFAULT_BASELINE_REPORT = Path("docs/iterations/0001-t06-retrieval-baseline.json")
_ITERATION_PATTERN = re.compile(r"^\d{4}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TOGGLE_FIELDS = RETRIEVAL_TOGGLE_FIELDS

# 门禁默认阈值(T09):覆盖无密钥(CI)可复现的指标子集,质检守下限防回归。
# 配套运行口径:确定性替身 + --min-score 0.07(替身分数分布与生产 bge-m3 不同,
# 0.35 是生产向量的操作点;替身下 0.067 才能分开"知识库没有的问题"与真问题,
# 曲线数据见 docs/iterations/0003 记录)。
# 上游模式(真实 embedder + 重排 + LLM 生成)会用 --min-* 抬到更高目标,
# 例如 spec 参考阈值 Recall@3 ≥ 0.85 / MRR ≥ 0.75 / 忠实度 ≥ 0.9。
DEFAULT_GATE_THRESHOLDS: dict[str, float] = {
    "recall": 0.75,
    "mrr": 0.70,
    "ndcg": 0.70,
    "hit_rate": 0.75,
    "faithfulness": 0.90,
    "citation_accuracy": 0.60,
    "refusal_correctness": 0.95,
    "permission_pass_rate": 1.0,
}

# 消融矩阵:教材第 43 章生产替换清单的开关组合;E/F/G 行验证各能力的边际收益。
ABLATION_COMBOS: list[tuple[str, dict[str, bool]]] = [
    ("A 全关(基线复现)", {}),
    ("B 混合开", {"use_hybrid": True}),
    ("C 重排开(混合关)", {"use_rerank": True}),
    ("D 混合+重排开", {"use_hybrid": True, "use_rerank": True}),
    ("E 混合+重排+MMR", {"use_hybrid": True, "use_rerank": True, "use_mmr": True}),
    ("F 混合+重排+查询改写", {"use_hybrid": True, "use_rerank": True, "use_query_rewrite": True}),
    ("G 混合+重排+多查询扩展", {"use_hybrid": True, "use_rerank": True, "use_multi_query": True}),
    ("H 重排+查询改写(混合关)", {"use_rerank": True, "use_query_rewrite": True}),
]

_TOGGLE_FIELDS = (
    "use_query_rewrite",
    "use_multi_query",
    "use_hybrid",
    "use_mmr",
    "use_rerank",
)


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
        "--vector-store",
        choices=("memory", "chroma", "pgvector"),
        default="memory",
        help="进程内模式的向量库后端:memory=基线(默认),chroma=持久向量库(T12),"
        "pgvector=PostgreSQL 生产持久化(T13,DATABASE_URL 指向的库,整库覆盖式写入"
        "评估语料,评估结束不清理)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL 连接串(pgvector 评估与 --record-run 留档用:"
        "postgresql://<用户>:<密码>@<主机>:5432/<库>);缺省用应用配置 DATABASE_URL",
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
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="T07 消融:同一数据集重跑开关组合矩阵(进程内模式,重排组合需密钥)",
    )
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=DEFAULT_BASELINE_REPORT,
        help="T06 基线 JSON 路径,用于差值表(缺省指向仓库基线报告)",
    )
    generator = parser.add_argument_group("生成链路(T09)")
    generator.add_argument(
        "--qa-generator",
        choices=("rule", "llm"),
        default=None,
        help="回答生成器:rule=规则版(无密钥可复现,默认);llm=真实 LLM(需密钥)。"
        "缺省按是否配置密钥解析",
    )
    gate = parser.add_argument_group("CI 门禁")
    parser.add_argument(
        "--check",
        action="store_true",
        help="在报告产出后对门禁阈值断言,跌破即失败(exit 1);红灯验证用 --min-* 拉高阈值",
    )
    for flag in _GATE_FLAGS:
        gate.add_argument(
            f"--min-{flag.replace('_', '-')}",
            type=float,
            default=None,
            help=GATE_FLAG_HELP[flag],
        )
    downstream = parser.add_argument_group("高级检索开关(BooleanOptionalAction)")
    for _field, spec in _TOGGLE_SPECS.items():
        downstream.add_argument(spec["flag"], action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--use-app-defaults",
        action="store_true",
        help="检索开关默认沿用应用配置(需密钥的组合会做前置校验);缺省全关,T06 基线口径",
    )
    persistence = parser.add_argument_group("生产持久化留档(T13)")
    persistence.add_argument(
        "--record-run",
        action="store_true",
        help="把本次评估报告留档进 evaluation_runs 表(PostgreSQL,需 --database-url"
        "或应用配置);报告文件照旧落盘,留档供跨迭代追溯",
    )
    return parser.parse_args(argv)


_TOGGLE_SPECS: dict[str, dict[str, str]] = {
    # Settings 开关字段 → CLI 属性名与开关;快照、覆盖与组合复现命令共用,
    # 避免清单在多处漂移。
    "use_query_rewrite": {"attr": "rewrite", "flag": "--rewrite"},
    "use_multi_query": {"attr": "multi_query", "flag": "--multi-query"},
    "use_hybrid": {"attr": "hybrid", "flag": "--hybrid"},
    "use_mmr": {"attr": "mmr", "flag": "--mmr"},
    "use_rerank": {"attr": "rerank", "flag": "--rerank"},
}
_RETRIEVAL_PARAM_FIELDS = (
    "retrieval_vector_weight",
    "retrieval_keyword_weight",
    "retrieval_candidate_k",
    "retrieval_multi_query_count",
    "retrieval_mmr_lambda",
)
# 需要真实上游的开关:无密钥时不得进评估(会变成静默降级数据)
_UPSTREAM_KEYED_FIELDS = ("use_query_rewrite", "use_multi_query", "use_rerank")

# 门禁阈值 CLI 旗标 → 报告字段;与 DEFAULT_GATE_THRESHOLDS 同序
GATE_FLAG_HELP: dict[str, str] = {
    "recall": "Recall@K 下限(缺省用门禁默认值)",
    "mrr": "MRR 下限",
    "ndcg": "NDCG@K 下限",
    "hit_rate": "HitRate@K 下限",
    "faithfulness": "忠实度(规则法)下限",
    "citation_accuracy": "引用准确率(规则法)下限",
    "refusal_correctness": "拒答正确率(生成口径·双向)下限",
    "permission_pass_rate": "权限边界通过率下限",
}
_GATE_FLAGS = tuple(GATE_FLAG_HELP)


def gate_thresholds(args: argparse.Namespace) -> dict[str, float]:
    """--check 生效的阈值:默认值打底,显式 --min-* 覆盖(阈值可调)。"""
    thresholds = dict(DEFAULT_GATE_THRESHOLDS)
    for flag in _GATE_FLAGS:
        value = getattr(args, f"min_{flag}")
        if value is not None:
            thresholds[flag] = float(value)
    return thresholds


def gate_checks(report: EvaluationReport, thresholds: dict[str, float]):
    """门禁逐项核对:(名称, 实测值, 下限)。实测值缺失按未达标处理。"""
    checks = [
        (f"Recall@{report.k}", report.summary.recall_at_k, thresholds["recall"]),
        ("MRR", report.summary.mrr, thresholds["mrr"]),
        (f"NDCG@{report.k}", report.summary.ndcg_at_k, thresholds["ndcg"]),
        (f"HitRate@{report.k}", report.summary.hit_rate, thresholds["hit_rate"]),
    ]
    # 生成指标块缺失时按未达标处理:GenerationMetrics() 的 None 手段
    # 让忠实度 / 引用 / 拒答门禁走「缺失阈值数据」的 ✗ 分支,绝不静默放行。
    generation = report.generation or GenerationMetrics()
    checks.extend(
        [
            ("忠实度(规则法)", generation.faithfulness, thresholds["faithfulness"]),
            ("引用准确率(规则法)", generation.citation_accuracy, thresholds["citation_accuracy"]),
            (
                "拒答正确率(生成口径)",
                generation.refusal_correctness,
                thresholds["refusal_correctness"],
            ),
        ]
    )
    checks.append(
        ("权限边界通过率", report.permission_pass_rate, thresholds["permission_pass_rate"])
    )
    return checks


def run_gate(report: EvaluationReport, thresholds: dict[str, float]) -> bool:
    """打印门禁核对表;跌破下限 → False(CI 红灯的依据)。"""
    passed = True
    for name, actual, floor in gate_checks(report, thresholds):
        if actual is None:
            print(f"[gate] {name} 缺失阈值数据(< {floor:.4f})✗", file=sys.stderr)
            passed = False
            continue
        ok = actual + 1e-9 >= floor
        passed = passed and ok
        stream = sys.stdout if ok else sys.stderr
        print(
            f"[gate] {name} {actual:.4f} ≥ {floor:.4f} "
            f"{'✓' if ok else '✗ 跌破门禁'}",
            file=stream,
        )
    print(f"[gate] 门禁结论:{'通过' if passed else '未通过'}", file=sys.stderr)
    return passed


def resolve_qa_generator(settings: Settings, args: argparse.Namespace) -> str | None:
    """生成器选择:显式 --qa-generator 优先且 ``llm`` 必须有密钥;缺省规则版。

    规则版缺省(即便本地配了密钥)是为了让评估默认零成本、逐字复现;
    要评真实 LLM 生成质量,显式传 ``--qa-generator llm``。
    """
    if args.qa_generator == "llm" and not settings.upstream_configured:
        return None  # main() 负责报错退出
    return args.qa_generator or "rule"


def _vector_store_updates(args: argparse.Namespace, temp_dir: Path) -> dict[str, Any]:
    """进程内评估的向量库后端配置:memory 沿用默认,chroma 数据落临时目录。

    pgvector:评估语料写入连接串指向的库(表在启动时幂等建好);语料按文档
    整组覆盖,评估之间不做整库清空 —— 供跨次对比沿用同一库,靠新上传的
    文档 id 区隔;要干净口径请指向评估专用库。
    """
    if args.vector_store == "chroma":
        return {"vector_store": "chroma", "chroma_path": temp_dir / "chroma"}
    if args.vector_store == "pgvector":
        updates: dict[str, Any] = {"vector_store": "pgvector"}
        if args.database_url:
            updates["database_url"] = args.database_url
        return updates
    return {}


def _release_vector_store(app) -> None:
    """清掉向量库后端持有的资源(chroma 文件句柄 / pgvector 连接池);

    pgvector 模式的连接池为仓储与向量库共享,close 幂等。
    """
    store = getattr(app.state, "vector_store", None)
    close = getattr(store, "close", None)
    if callable(close):
        close()


def _effective_search_toggles(
    settings: Settings, args: argparse.Namespace
) -> tuple[dict[str, bool], dict[str, Any]]:
    """有效检索开关:默认全关(与 T06 基线同口径、CI 可复现);``--use-app-defaults``
    改用应用默认组合,再被显式开关覆盖。返回(应用到应用配置的覆盖, 快照)。
    """
    explicit = _settings_updates(args)
    if args.use_app_defaults:
        base = {field: bool(getattr(settings, field)) for field in _TOGGLE_FIELDS}
    else:
        base = {field: False for field in _TOGGLE_FIELDS}
    base.update(explicit)
    params = {key: getattr(settings, key) for key in _RETRIEVAL_PARAM_FIELDS}
    snapshot = {**base, **params}
    return base, snapshot


def _settings_updates(args: argparse.Namespace) -> dict[str, Any]:
    """显式开关 → 应用配置覆盖;None 表示沿用默认组合。"""
    return {
        field: getattr(args, spec["attr"])
        for field, spec in _TOGGLE_SPECS.items()
        if getattr(args, spec["attr"]) is not None
    }


def _retrieval_snapshot(settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    """记录进报告的检索开关快照:默认值 + 显式覆盖后的有效值。"""
    snapshot: dict[str, Any] = {
        field: getattr(settings, field) for field in _TOGGLE_FIELDS
    }
    for key in _RETRIEVAL_PARAM_FIELDS:
        snapshot[key] = getattr(settings, key)
    snapshot.update(overrides)
    return snapshot


def _load_baseline(path: Path, *, warn: bool = True) -> EvaluationReport | None:
    if not path.is_file():
        return None
    try:
        return EvaluationReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # 基线报告损坏不应阻断本次评估
        if warn:
            print(f"基线报告不可读({path}):{exc}", file=sys.stderr)
        return None


async def _run(
    args: argparse.Namespace,
    dataset,
    config: EvaluationConfig,
    settings_updates: dict[str, Any],
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

    base = get_settings()
    with tempfile.TemporaryDirectory(prefix="rag-evaluation-") as temp_dir:
        updates: dict[str, Any] = {
            "upload_dir": Path(temp_dir),
            # 评估固定同步入库口径:语料切片参数由数据集逐文档显式指定,
            # 异步管线的默认参数自动入库会打乱可比性(T14 起,见 ADR-0012)
            "ingestion_async": False,
            **settings_updates,
            **_vector_store_updates(args, Path(temp_dir)),
        }
        if not args.verbose:
            updates.update({"log_level": "WARNING", "log_format": "console"})
        settings = base.model_copy(update=updates)
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
            _release_vector_store(app)


async def _run_ablation(
    args: argparse.Namespace,
    dataset,
    embedder_label: str,
    combo_commands: dict[str, str],
) -> tuple[list[tuple[str, EvaluationReport]], list[str]]:
    """消融矩阵 runner:每行固定全关基线 + 该行的显式开关,互不泄漏。"""
    base = get_settings()
    toggle_zero_base = {field: False for field in _TOGGLE_FIELDS}
    toggle_zero_base["qa_generator"] = resolve_qa_generator(base, args) or "rule"
    runs: list[tuple[str, EvaluationReport]] = []
    skipped: list[str] = []
    dataset_dir = args.dataset.resolve().parent
    for label, overrides in ABLATION_COMBOS:
        if overrides.get("use_rerank") and not base.upstream_configured:
            skipped.append(
                f"{label}(跳过:重排需真实上游密钥,不与替身时序静默降级混比)"
            )
            continue
        combo_updates = {**toggle_zero_base, **overrides}
        settings = base.model_copy(update=combo_updates)
        combo_config = EvaluationConfig(
            k=args.k,
            top_k=args.top_k,
            min_score=(
                args.min_score if args.min_score is not None else settings.qa_min_score
            ),
            chunk_strategy=ChunkingStrategy(args.strategy) if args.strategy else None,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            embedder=embedder_label,
            mode="in-process",
            vector_store=args.vector_store,
            command=combo_commands[label],
            run_name=f"{args.run_name} · {label}",
            retrieval_config=_retrieval_snapshot(settings, overrides),
        )
        report = await _run_combo(
            dataset,
            dataset_dir=dataset_dir,
            settings_updates=combo_updates,
            config=combo_config,
            args=args,
        )
        runs.append((label, report))
        print(
            f"[{label}] Recall@{report.k}={report.summary.recall_at_k:.4f} "
            f"MRR={report.summary.mrr:.4f}"
        )
    return runs, skipped


async def _run_combo(
    dataset,
    *,
    dataset_dir: Path,
    settings_updates: dict[str, Any],
    config: EvaluationConfig,
    args: argparse.Namespace,
) -> EvaluationReport:
    base = get_settings()
    with tempfile.TemporaryDirectory(prefix="rag-evaluation-") as temp_dir:
        updates: dict[str, Any] = {
            "upload_dir": Path(temp_dir),
            # 评估固定同步入库口径:语料切片参数由数据集逐文档显式指定,
            # 异步管线的默认参数自动入库会打乱可比性(T14 起,见 ADR-0012)
            "ingestion_async": False,
            **settings_updates,
            **_vector_store_updates(args, Path(temp_dir)),
        }
        updates.setdefault("log_level", "WARNING")
        updates.setdefault("log_format", "console")
        settings = base.model_copy(update=updates)
        app = create_app(settings, embedder=DeterministicEmbedder(dim=EVALUATION_DIM))
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://evaluation", timeout=120.0
            ) as client:
                service = EvaluationService(
                    client=client,
                    dataset=dataset,
                    dataset_dir=dataset_dir,
                    config=config,
                )
                return await service.run()
        finally:
            await app.state.llm_client.aclose()
            _release_vector_store(app)


def _record_evaluation_run(report: EvaluationReport, dsn: str, *, run_id: str) -> None:
    """把评估报告留档进 evaluation_runs 表;失败给出警告但不改变评估结果。"""
    from app.repositories.postgres import PgDatabase, PgEvaluationRunRepository

    try:
        db = PgDatabase(dsn)
        try:
            PgEvaluationRunRepository(db).add(report, run_id=run_id)
        finally:
            db.close()
    except Exception as exc:
        print(f"评估运行留档失败({run_id}):{exc}", file=sys.stderr)


def settings_database_url() -> str | None:
    url = get_settings().database_url
    return url if url and url.strip() else None


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
    if args.ablate and args.base_url:
        print("--ablate 只支持进程内模式(消融矩阵需要逐组合重建应用)", file=sys.stderr)
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
    if args.base_url and args.vector_store in ("chroma", "pgvector"):
        print(
            "--vector-store 只支持进程内模式:外部模式的后端由服务端自己的配置决定",
            file=sys.stderr,
        )
        return 2
    if args.record_run and args.database_url is None and not settings_database_url():
        print(
            "--record-run 需要 PostgreSQL 连接串:显式 --database-url 或应用配置 DATABASE_URL",
            file=sys.stderr,
        )
        return 2

    settings = get_settings()
    effective_toggles, snapshot = _effective_search_toggles(settings, args)
    # 涉及真实上游的重排 / LLM 规划开关,无密钥时不得进入评估(替换成静默降级数据)
    for field in _UPSTREAM_KEYED_FIELDS:
        if effective_toggles[field] and not settings.upstream_configured:
            flag = _TOGGLE_SPECS[field]["flag"]
            print(
                f"有效检索配置包含 {field}(经 {flag} 或 --use-app-defaults),"
                "需要真实上游密钥;先设置 SILICONFLOW_API_KEY,不带病出报告",
                file=sys.stderr,
            )
            return 2

    qa_generator = resolve_qa_generator(settings, args)
    if qa_generator is None:
        print(
            "--qa-generator llm 需要真实上游密钥;先设置 SILICONFLOW_API_KEY,"
            "或用规则版基线(产出无密钥可复现的生成指标)",
            file=sys.stderr,
        )
        return 2
    # 无密钥环境强制规则版,与「生成指标 CI 子集」口径一致(见 evaluation/README)
    min_score = args.min_score if args.min_score is not None else settings.qa_min_score
    # 拒答阈值统一进应用配置:检索口径与 /chat 的生成口径用同一把尺子,
    # 确定性替身的分数分布与生产 bge-m3 不同,阈值随 embedder 口径调整(报告已记录)。
    global_updates = {
        **effective_toggles,
        "qa_generator": qa_generator,
        "qa_min_score": min_score,
    }

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
    if args.check and args.ablate:
        print("门禁只对单次运行生效:--check 与 --ablate 不可同时使用", file=sys.stderr)
        return 2

    dataset_path = args.dataset.resolve()
    try:
        dataset = load_golden_dataset(dataset_path)
    except Exception as exc:  # 数据集文件缺失 / YAML / 字段校验失败
        print(f"读取黄金数据集失败:{exc}", file=sys.stderr)
        return 1

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
        vector_store=None if args.base_url else args.vector_store,
        command=command.strip(),
        run_name=args.run_name,
        retrieval_config=snapshot,
    )
    baseline = _load_baseline(args.baseline_report)
    # --record-run 只留档单次运行(消融矩阵逐组合另行留档,不在本入口)
    single_report: EvaluationReport | None = None

    try:
        if args.ablate:
            combo_commands = {
                label: _combo_command(raw_args, overrides)
                for label, overrides in ABLATION_COMBOS
            }
            runs, skipped = asyncio.run(
                _run_ablation(args, dataset, embedder, combo_commands)
            )
            if not runs:
                print("消融没有任何组合成功运行", file=sys.stderr)
                return 1
            markdown = render_ablation_markdown(
                args.run_name,
                dataset,
                config,
                runs,
                skipped=skipped,
                baseline=baseline,
            )
            json_text = render_ablation_json(runs, skipped)
            summary = runs[-1][1].summary
            k = config.k
        else:
            report = asyncio.run(_run(args, dataset, config, global_updates))
            markdown = render_markdown(report, baseline=baseline)
            json_text = render_json(report)
            summary = report.summary
            k = report.k
            single_report = report
    except (EvaluationApiError, EvaluationDatasetError) as exc:
        print(f"评估失败:{exc}", file=sys.stderr)
        return 1

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8", newline="\n")
    json_report_path.parent.mkdir(parents=True, exist_ok=True)
    json_report_path.write_text(json_text, encoding="utf-8", newline="\n")

    if args.record_run and single_report is not None:
        dsn = args.database_url or settings_database_url()
        run_id = f"run_{uuid4().hex[:12]}"
        _record_evaluation_run(single_report, dsn, run_id=run_id)
        print(f"评估运行已留档:run_id={run_id}")

    print(
        f"用例 {summary.cases} 条 | "
        f"Recall@{k}={summary.recall_at_k:.4f} | "
        f"MRR={summary.mrr:.4f} | "
        f"NDCG@{k}={summary.ndcg_at_k:.4f}"
    )
    print(f"报告:{report_path}")
    print(f"JSON:{json_report_path}")
    if args.check and not run_gate(report, gate_thresholds(args)):
        print("指标跌破门禁:CI 红灯(报告已落盘,便于回看)", file=sys.stderr)
        return 1
    return 0


def _combo_command(raw_args: list[str], overrides: dict[str, bool]) -> str:
    """组合复现命令:全局参数 + 显式开关覆盖。

    全局开关若与本组合冲突,组合值已在评估时生效;命令行二选一展示组合的
    显式形态,保证可复现性以这份命令为准。
    """
    parts = ["uv run python scripts/evaluate.py"]
    skip_next = False
    for arg in raw_args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--ablate", "--baseline-report"):
            skip_next = arg == "--baseline-report"
            continue
        parts.append(arg)
    for field, spec in _TOGGLE_SPECS.items():
        flag = spec["flag"]
        if overrides.get(field):
            parts.append(flag)
        else:
            parts.append("--no-" + flag[2:])
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
