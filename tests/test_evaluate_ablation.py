"""评估 CLI 的 T07 消融缝:开关组合矩阵、跳过标注与基线差值(不触网)。

CLI 在进程内模式经 get_settings() 读真实 .env;测试一律注入无密钥配置,
既不触网也不计费;重排组合在无密钥时的跳过路径由本文件覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.evaluate as evaluate_module
from app.core.config import Settings
from app.domain.evaluation import EvaluationReport, GoldenDataset, RetrievalMetrics
from app.services.evaluation import EvaluationConfig, render_ablation_markdown
from scripts.evaluate import main

DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "golden_dataset.yaml"


@pytest.fixture(autouse=True)
def no_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试里禁读 .env / 真实密钥:评估器全部在无密钥快照下运行。"""

    def _isolated_settings() -> Settings:
        return Settings(
            _env_file=None,
            environment="test",
            siliconflow_api_key=None,
        )

    monkeypatch.setattr(evaluate_module, "get_settings", _isolated_settings)


def _cli_args(tmp_path: Path, *, extra: list[str]) -> list[str]:
    report = tmp_path / "t07-report.md"
    json_report = tmp_path / "t07-report.json"
    return [
        "--dataset",
        str(DATASET_PATH),
        "--report",
        str(report),
        "--json-report",
        str(json_report),
        "--iteration",
        "0002",
        "--slug",
        "t07-ablation-test",
        "--run-name",
        "T07 消融测试",
        *extra,
    ]


def test_cli_ablation_without_key_runs_no_upstream_combos_and_skips_rerank(
    tmp_path: Path,
) -> None:
    report = tmp_path / "t07-report.md"
    json_report = tmp_path / "t07-report.json"

    code = main(_cli_args(tmp_path, extra=["--ablate"]))

    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "## 消融对比" in text
    assert "A 全关(基线复现)" in text
    assert "B 混合开" in text
    # 无密钥:LLM / 重排相关组合显式标注跳过,绝不静默降级出数
    assert "C 重排开(混合关)" in text
    assert "跳过:重排需真实上游密钥" in text
    assert "ΔRecall vs 基线" in text
    assert "默认组合依据" in text

    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["kind"] == "ablation"
    labels = [run["label"] for run in payload["runs"]]
    assert labels == ["A 全关(基线复现)", "B 混合开"]
    assert len(payload["skipped"]) == 6

    # 对照基线:A 组合全关 = T06 单向量时序,指标必须逐字复现(19/24、18/24)
    a_summary = payload["runs"][0]["report"]["summary"]
    assert a_summary["recall_at_k"] == pytest.approx(19 / 24, abs=1e-6)
    assert a_summary["mrr"] == pytest.approx(18 / 24, abs=1e-6)


def test_cli_records_retrieval_toggle_snapshot(tmp_path: Path) -> None:
    report = tmp_path / "single.md"
    json_report = tmp_path / "single.json"

    code = main(
        _cli_args(
            tmp_path,
            extra=[
                "--report",
                str(report),
                "--json-report",
                str(json_report),
                "--hybrid",
                "--no-rerank",
                "--no-mmr",
                "--no-rewrite",
                "--no-multi-query",
            ],
        )
    )

    assert code == 0
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    toggles = payload["retrieval_config"]
    assert toggles["use_hybrid"] is True
    assert toggles["use_rerank"] is False
    text = report.read_text(encoding="utf-8")
    assert "| 检索配置 |" in text
    assert "hybrid=on" in text
    assert "rerank=off" in text
    # 单次运行默认带与 T06 基线的差值表
    assert "## 与基线对比" in text


def test_cli_rejects_rerank_toggle_without_key(tmp_path: Path) -> None:
    code = main(_cli_args(tmp_path, extra=["--rerank"]))

    assert code == 2


def test_cli_use_app_defaults_requires_upstream_key(tmp_path: Path) -> None:
    """应用默认组合含重排:无密钥时的 --use-app-defaults 必须提前失败。"""
    code = main(_cli_args(tmp_path, extra=["--use-app-defaults"]))

    assert code == 2


def test_cli_use_app_defaults_respects_explicit_overrides(tmp_path: Path) -> None:
    """--use-app-defaults 可以被显式开关降档:无上游的组合照样能出可信报告。"""
    report = tmp_path / "app-defaults.md"
    json_report = tmp_path / "app-defaults.json"

    code = main(
        _cli_args(
            tmp_path,
            extra=[
                "--report",
                str(report),
                "--json-report",
                str(json_report),
                "--use-app-defaults",
                "--no-rerank",
                "--no-rewrite",
            ],
        )
    )

    assert code == 0
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["retrieval_config"]["use_rerank"] is False
    text = report.read_text(encoding="utf-8")
    assert "rerank=off" in text


def test_cli_rejects_ablation_with_external_mode(tmp_path: Path) -> None:
    code = main(_cli_args(tmp_path, extra=["--ablate", "--base-url", "http://127.0.0.1:9"]))

    assert code == 2


# ===== 默认组合取舍规则(纯渲染层,不触网) =====


def _report(report_name: str, config: dict) -> EvaluationReport:
    metrics = RetrievalMetrics(
        recall_at_k=1.0, mrr=1.0, ndcg_at_k=1.0, hit_rate=1.0, cases=2
    )
    return EvaluationReport(
        run_name=report_name,
        dataset_name="d",
        dataset_version=1,
        k=3,
        top_k=3,
        min_score=0.35,
        chunk_strategy="table_aware",
        chunk_size=500,
        chunk_overlap=50,
        embedder="stub",
        mode="in-process",
        command="cmd",
        retrieval_config=config,
        cases_total=2,
        summary=metrics,
    )


def _tiny_dataset() -> GoldenDataset:
    return GoldenDataset.model_validate(
        {
            "name": "d",
            "version": 1,
            "corpus": [{"path": "corpus/x.md", "tenant": "acme"}],
            "cases": [
                {
                    "id": "c1",
                    "question": "q",
                    "category": "product",
                    "expected_chunks": [{"document": "x.md", "anchor": "锚"}],
                }
            ],
        }
    )


def test_default_choice_prefers_leanest_tied_combo() -> None:
    """并列最优时默认依据取上游开销最小的开关组合(F=G=H → 同分取少行)。"""
    lean = _report("lean", {"use_rerank": True, "use_query_rewrite": True})
    heavy = _report(
        "heavy",
        {"use_hybrid": True, "use_rerank": True, "use_query_rewrite": True},
    )

    text = render_ablation_markdown(
        "消融", _tiny_dataset(), EvaluationConfig(), [("heavy", heavy), ("lean", lean)]
    )

    choice_line = next(
        line for line in text.splitlines() if line.startswith("**默认组合依据**")
    )
    assert "「lean」" in choice_line
    assert "「heavy」" not in choice_line
