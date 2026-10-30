"""生成指标评估:评估器经 /api/v1/chat 主缝驱动生成链路(教材第 44 章,T09)。

生成指标(忠实度 / 引用准确率 / 拒答正确率·生成口径)必须与检索指标在
同一轮评估里产出;语料是合成小文档,规则版生成器走「最相关切片原文」
的抽取式路径,回答内容直接来自所引资料,便于断言期望值。
"""

from __future__ import annotations

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
from app.services.evaluation import EvaluationConfig, EvaluationService

CORPUS = """# 售后服务制度

## 未发货退款

订单尚未发货时,客户可以申请取消订单并全额退款,退款将在三个工作日内原路退回。

## 已发货退款

订单已经发货后,客户需要先签收商品,再发起退货退款申请。
"""


def _dataset(tmp_path: Path, extra_cases: list[GoldenCase]) -> GoldenDataset:
    corpus = tmp_path / "after_sales.md"
    corpus.write_text(CORPUS, encoding="utf-8")
    base_cases = [
        GoldenCase(
            id="refund_unshipped",
            question="没发货可以退钱吗?",
            category=EvaluationCategory.REFUND,
            expected_chunks=[ExpectedChunk(document="after_sales.md", anchor="尚未发货")],
            filters={"tenant": "acme"},
        ),
        GoldenCase(
            id="unknown_shuttle",
            question="公司班车路线是什么?",
            category=EvaluationCategory.UNKNOWN,
            expected_chunks=[],
            filters={"tenant": "acme"},
            should_refuse=True,
        ),
    ]
    return GoldenDataset(
        name="generation-test",
        version=1,
        corpus=[CorpusDocument(path="after_sales.md", tenant="acme")],
        cases=[*base_cases, *extra_cases],
    )


async def _run(settings, dataset: GoldenDataset, tmp_path: Path):
    app = create_app(settings, embedder=DeterministicEmbedder(dim=EVALUATION_DIM))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://evaluation") as client:
        service = EvaluationService(
            client=client,
            dataset=dataset,
            dataset_dir=tmp_path,
            config=EvaluationConfig(k=3, min_score=settings.qa_min_score),
        )
        return await service.run()


async def test_generation_metrics_flow_through_chat_seam(settings, tmp_path) -> None:
    dataset = _dataset(tmp_path, [])
    report = await _run(settings, dataset, tmp_path)

    gen = report.generation
    assert gen is not None
    assert gen.cases_total == 2
    assert gen.answered == 1
    # 抽取式回答 = 所引资料原文 → 忠实度与引用准确率满分
    assert gen.faithfulness == pytest.approx(1.0)
    assert gen.citation_accuracy == pytest.approx(1.0)
    # 拒答用例被正确拒答;可回答用例被回答 → 双向判定全对
    assert gen.refusal_correctness == pytest.approx(1.0)
    assert gen.answer_rate == pytest.approx(1.0)
    # 报告留痕:应用配置里观测到的生成器是规则版
    assert report.generator == "rule_based"

    answered = next(r for r in report.case_results if r.case_id == "refund_unshipped")
    assert answered.refused is False
    assert "根据知识库资料" in answered.answer
    assert answered.faithfulness == pytest.approx(1.0)
    assert answered.citation_accuracy == pytest.approx(1.0)
    assert answered.unsupported_claims == []
    assert [citing.document for citing in answered.citations] == ["after_sales.md"]

    refused = next(r for r in report.case_results if r.case_id == "unknown_shuttle")
    assert refused.refused is True
    assert refused.faithfulness is None
    assert refused.citation_accuracy is None
    assert refused.citations == []


async def test_generation_metrics_come_out_per_category(settings, tmp_path) -> None:
    dataset = _dataset(tmp_path, [])
    report = await _run(settings, dataset, tmp_path)

    refund = report.generation_categories["refund"]
    assert refund.faithfulness == pytest.approx(1.0)
    assert refund.citation_accuracy == pytest.approx(1.0)
    unknown = report.generation_categories["unknown"]
    assert unknown.answered == 0
    assert unknown.refusal_correctness == pytest.approx(1.0)


async def test_answerable_case_refused_by_generation_is_flagged(
    settings, tmp_path
) -> None:
    """可回答用例被生成链路拒答 → answer_rate / 拒答正确率(双向)被压低。"""
    wrongly_refused = GoldenCase(
        id="wrong_refusal",
        # 问题词面与语料毫无交集:检索低分 → 链路拒答,但该用例应可回答
        question="紫金山索道的营运时间?",
        category=EvaluationCategory.REFUND,
        expected_chunks=[ExpectedChunk(document="after_sales.md", anchor="尚未发货")],
        filters={"tenant": "acme"},
        notes="故意构造错误拒答",
    )
    dataset = _dataset(tmp_path, [wrongly_refused])
    report = await _run(settings, dataset, tmp_path)

    gen = report.generation
    assert gen.answered == 1
    assert gen.wrongly_refused == 1
    assert gen.answer_rate == pytest.approx(1 / 2)
    # 拒答用例正确 + 可回答用例被错拒 → 全部用例的双向判定 2/3
    assert gen.refusal_correctness == pytest.approx(2 / 3)
    # 忠实度均值只统计真正作答的用例:错拒用例不计入忠实度
    assert gen.faithfulness == pytest.approx(1.0)

    case = next(r for r in report.case_results if r.case_id == "wrong_refusal")
    assert case.refused is True
    assert case.faithfulness is None
    assert case.recall_at_k is not None  # 检索侧仍然照常给出指标


async def test_markdown_and_json_reports_include_generation_metrics(
    settings, tmp_path
) -> None:
    import json

    from app.services.evaluation import render_json, render_markdown

    dataset = _dataset(tmp_path, [])
    report = await _run(settings, dataset, tmp_path)

    markdown = render_markdown(report)
    assert markdown == render_markdown(report)  # 生成段也同样逐字节可复现
    assert "忠实度" in markdown
    assert "引用准确率" in markdown
    assert "拒答正确率" in markdown
    assert "作答率" in markdown
    assert "rule_based" in markdown  # 观测到的生成器写进报告

    payload = json.loads(render_json(report))
    assert payload["generator"] == "rule_based"
    assert payload["generation"]["faithfulness"] == pytest.approx(1.0)
    assert payload["generation"]["refusal_correctness"] == pytest.approx(1.0)
    assert payload["generation_categories"]["refund"]["citation_accuracy"] == pytest.approx(1.0)
    answered = next(
        r for r in payload["case_results"] if r["case_id"] == "refund_unshipped"
    )
    assert answered["refused"] is False
    assert answered["answer"]
    assert {"document", "index"} <= set(answered["citations"][0])
