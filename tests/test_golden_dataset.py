"""黄金数据集的版本化契约:覆盖度、唯一性与可定位性(教材第 44 章)。"""

from __future__ import annotations

from pathlib import Path

from app.services.evaluation import load_golden_dataset

DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "golden_dataset.yaml"


def test_golden_dataset_meets_coverage_contract() -> None:
    dataset = load_golden_dataset(DATASET_PATH)

    assert dataset.version >= 1
    assert len(dataset.cases) >= 20
    assert sum(case.should_refuse for case in dataset.cases) >= 2
    assert sum(bool(case.forbidden_documents) for case in dataset.cases) >= 1
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)

    for corpus in dataset.corpus:
        assert (DATASET_PATH.parent / corpus.path).is_file(), corpus.path


def test_golden_dataset_keeps_textbook_seven_sample_cases() -> None:
    """教材第 44 章的 7 条样例先移植,再扩展;ID 保持可追溯。"""
    dataset = load_golden_dataset(DATASET_PATH)
    ids = {case.id for case in dataset.cases}

    assert {
        "refund_unshipped",
        "refund_shipped",
        "logistics_delay",
        "invoice_enterprise",
        "leave_annual_tenure",
        "reimbursement_materials",
        "unknown_shuttle",
    } <= ids


def test_every_case_declares_expected_chunks_or_refusal() -> None:
    dataset = load_golden_dataset(DATASET_PATH)

    for case in dataset.cases:
        assert case.expected_answer_keywords, case.id
        if case.should_refuse:
            assert case.expected_chunks == [], case.id
        else:
            assert case.expected_chunks, case.id
            for expected in case.expected_chunks:
                assert expected.document in {Path(doc.path).name for doc in dataset.corpus}
