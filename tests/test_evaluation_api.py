"""评估器主接缝:经 HTTP API 完成入库与检索,产出可逐条追溯的指标报告。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.domain.evaluation import (
    CorpusDocument,
    EvaluationCategory,
    ExpectedChunk,
    GoldenCase,
    GoldenDataset,
)
from app.integrations.llm.deterministic import EVALUATION_DIM, DeterministicEmbedder
from app.main import create_app
from app.services.evaluation import (
    EvaluationConfig,
    EvaluationDatasetError,
    EvaluationService,
    load_golden_dataset,
    render_json,
    render_markdown,
)

DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "golden_dataset.yaml"


@pytest.fixture
def golden_dataset():
    return load_golden_dataset(DATASET_PATH)


async def _run_evaluation(settings, dataset, *, k: int = 3):
    app = create_app(settings, embedder=DeterministicEmbedder(dim=EVALUATION_DIM))
    transport = httpx.ASGITransport(app=app)
    config = EvaluationConfig(
        k=k,
        min_score=settings.qa_min_score,
        embedder="deterministic-lexical-1024d",
        mode="in-process",
        command="uv run python scripts/evaluate.py --dataset evaluation/golden_dataset.yaml",
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://evaluation") as client:
        service = EvaluationService(
            client=client,
            dataset=dataset,
            dataset_dir=DATASET_PATH.parent,
            config=config,
        )
        return await service.run()


async def test_evaluation_runs_through_api_and_keeps_case_traceability(
    settings, golden_dataset
) -> None:
    report = await _run_evaluation(settings, golden_dataset)

    assert report.cases_total == len(golden_dataset.cases) >= 20
    assert report.k == report.top_k == 3
    assert {result.case_id for result in report.case_results} == {
        case.id for case in golden_dataset.cases
    }

    answerable = [result for result in report.case_results if not result.should_refuse]
    refusal = [result for result in report.case_results if result.should_refuse]
    assert report.summary.cases == len(answerable)
    assert report.summary.recall_at_k == pytest.approx(
        sum(result.recall_at_k for result in answerable) / len(answerable)
    )
    assert report.summary.mrr == pytest.approx(
        sum(result.mrr for result in answerable) / len(answerable)
    )
    assert report.refusal_accuracy == pytest.approx(
        sum(result.refusal_ok for result in refusal) / len(refusal)
    )

    for result in answerable:
        assert result.expected, result.case_id
        assert all(ref.chunk_id for ref in result.expected)
        assert [hit.rank for hit in result.retrieved] == list(
            range(1, len(result.retrieved) + 1)
        )
    for result in refusal:
        assert result.expected == []
        assert result.recall_at_k is None
        assert result.refusal_ok is not None

    # 至少一条用例真实命中,证明锚点解析与检索链路都工作
    assert any(result.recall_at_k == 1.0 for result in answerable)
    # 数据集必须有多切片用例,否则 Recall@K 与 HitRate@K 会退化成同一个指标
    assert any(len(result.expected) > 1 for result in answerable)
    assert report.category_case_counts["unknown"] == 2


async def test_permission_boundary_case_blocks_cross_tenant_chunk(
    settings, golden_dataset
) -> None:
    report = await _run_evaluation(settings, golden_dataset)
    result = next(
        item for item in report.case_results if item.case_id == "permission_tenant_isolation"
    )

    assert result.permission_ok is True
    assert result.forbidden_best_rank is not None
    assert all(hit.document != "globex_handbook.md" for hit in result.retrieved)
    assert any(ref.document == "employee_handbook.md" for ref in result.expected)


async def test_markdown_report_is_traceable_and_reproducible(settings, golden_dataset) -> None:
    report = await _run_evaluation(settings, golden_dataset)
    markdown = render_markdown(report)

    assert "Recall@3" in markdown
    assert "MRR" in markdown
    assert "NDCG@3" in markdown
    assert golden_dataset.name in markdown
    for case in golden_dataset.cases:
        assert case.id in markdown
    assert "无过滤对照" in markdown

    # 同样的报告再次渲染必须逐字节一致,便于后续迭代做 diff
    assert render_markdown(report) == markdown
    assert "chunk `" not in markdown


async def test_rerunning_evaluation_produces_byte_stable_reports(
    settings, golden_dataset
) -> None:
    first = await _run_evaluation(settings, golden_dataset)
    second = await _run_evaluation(settings, golden_dataset)

    assert render_markdown(first) == render_markdown(second)
    assert render_json(first) == render_json(second)
    payload = json.loads(render_json(first))
    assert all("chunk_id" not in item for item in payload["case_results"][0]["expected"])
    assert all("chunk_id" not in item for item in payload["case_results"][0]["retrieved"])


async def test_json_report_exposes_stable_fields_for_ci(settings, golden_dataset) -> None:
    report = await _run_evaluation(settings, golden_dataset)
    payload = json.loads(render_json(report))

    assert payload["schema_version"] == 1
    assert payload["dataset_name"] == golden_dataset.name
    assert payload["dataset_version"] == golden_dataset.version
    assert payload["summary"]["recall_at_k"] == pytest.approx(
        report.summary.recall_at_k
    )
    assert len(payload["case_results"]) == len(golden_dataset.cases)
    assert {
        "case_id",
        "question",
        "expected",
        "retrieved",
        "recall_at_k",
        "mrr",
        "ndcg_at_k",
        "refusal_ok",
        "permission_ok",
    } <= set(payload["case_results"][0])


async def test_dataset_anchor_without_a_match_fails_loudly(settings, tmp_path) -> None:
    corpus = tmp_path / "tiny.md"
    corpus.write_text("# 小文档\n\n这是一段用于测试的正文。\n", encoding="utf-8")
    dataset = GoldenDataset(
        name="bad-anchor",
        version=1,
        corpus=[CorpusDocument(path="tiny.md")],
        cases=[
            GoldenCase(
                id="bad_anchor_case",
                question="正文说了什么?",
                category=EvaluationCategory.LEAVE,
                expected_chunks=[ExpectedChunk(document="tiny.md", anchor="不存在的锚点")],
            )
        ],
    )
    app = create_app(settings, embedder=DeterministicEmbedder(dim=EVALUATION_DIM))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://evaluation") as client:
        service = EvaluationService(
            client=client,
            dataset=dataset,
            dataset_dir=tmp_path,
            config=EvaluationConfig(k=3, min_score=settings.qa_min_score),
        )
        with pytest.raises(EvaluationDatasetError, match="命中 0 个切片"):
            await service.run()
