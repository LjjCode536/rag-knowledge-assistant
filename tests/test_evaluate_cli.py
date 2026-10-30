"""CLI 主缝:一条命令在无密钥环境产出 Markdown + JSON 报告。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from scripts.evaluate import main

DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "golden_dataset.yaml"


@pytest.fixture
def keyless_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """门禁与生成器选择测试固定无密钥口径,不读 .env、不触真实上游。"""
    from scripts import evaluate as evaluate_mod

    settings = Settings(
        _env_file=None,
        environment="test",
        siliconflow_api_key=None,
        log_format="console",
        log_level="WARNING",
        qa_generator="rule",
    )
    monkeypatch.setattr(evaluate_mod, "get_settings", lambda: settings)
    return settings


def test_cli_writes_reports_without_api_key(tmp_path: Path) -> None:
    report = tmp_path / "baseline.md"
    json_report = tmp_path / "baseline.json"

    code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--report",
            str(report),
            "--json-report",
            str(json_report),
            "--run-name",
            "T06 测试基线",
        ]
    )

    assert code == 0
    assert report.is_file()
    assert json_report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "# T06 测试基线" in text
    assert "Recall@3" in text
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["cases_total"] >= 20
    assert payload["summary"]["cases"] >= 20


def test_cli_external_mode_requires_explicit_server_config(tmp_path: Path) -> None:
    common = [
        "--dataset",
        str(DATASET_PATH),
        "--report",
        str(tmp_path / "report.md"),
        "--json-report",
        str(tmp_path / "report.json"),
        "--base-url",
        "http://127.0.0.1:9",
    ]

    assert main(common) == 2
    assert main([*common, "--min-score", "0.35"]) == 2


def test_cli_custom_run_name_cannot_silently_overwrite_baseline() -> None:
    assert main(["--run-name", "T07 高级检索"]) == 2


def _gate_args(tmp_path: Path) -> list[str]:
    return [
        "--dataset",
        str(DATASET_PATH),
        "--report",
        str(tmp_path / "gate.md"),
        "--json-report",
        str(tmp_path / "gate.json"),
        "--min-score",
        "0.07",  # 确定性替身分数口径:生产 bge-m3 的操作点是 0.35
        "--check",
    ]


def test_cli_gate_passes_when_metrics_meet_keyless_thresholds(
    tmp_path: Path, keyless_settings: Settings
) -> None:
    """无密钥默认阈值下门禁绿灯:全指标一条命令产出,生成器留痕为规则版。"""
    assert main(_gate_args(tmp_path)) == 0
    payload = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    assert payload["generator"] == "rule_based"
    assert payload["generation"]["faithfulness"] is not None
    assert payload["generation"]["refusal_correctness"] is not None


def test_cli_gate_fails_below_threshold(
    tmp_path: Path, keyless_settings: Settings
) -> None:
    """人为把阈值拉到不可能的水平 → 红灯(exit 1),报告仍然落盘可查。"""
    report = tmp_path / "red.md"
    json_report = tmp_path / "red.json"

    code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--report",
            str(report),
            "--json-report",
            str(json_report),
            "--check",
            "--min-recall",
            "0.999",
        ]
    )

    assert code == 1
    assert report.is_file()  # 报告先落盘,红灯只是退出码与 stderr 提示


def test_cli_qa_generator_llm_requires_upstream_key(
    tmp_path: Path, keyless_settings: Settings
) -> None:
    common = [
        "--dataset",
        str(DATASET_PATH),
        "--report",
        str(tmp_path / "q.md"),
        "--json-report",
        str(tmp_path / "q.json"),
        "--qa-generator",
        "llm",
    ]

    assert main(common) == 2


# ===== 向量库后端切换(T12)=====


def test_cli_chroma_backend_runs_and_records_backend(
    tmp_path: Path, keyless_settings: Settings
) -> None:
    """--vector-store chroma 切换后端:进程内评估照常出报告,复现命令留痕。"""
    report = tmp_path / "chroma.md"
    json_report = tmp_path / "chroma.json"

    code = main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--report",
            str(report),
            "--json-report",
            str(json_report),
            "--run-name",
            "T12 向量库后端 Chroma",
            "--vector-store",
            "chroma",
        ]
    )

    assert code == 0
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["summary"]["cases"] >= 20
    assert payload["mode"] == "in-process"
    # 复现命令里已带 --vector-store,报告中留痕即可按命令复原这次口径
    assert "--vector-store chroma" in payload["command"]


def test_cli_chroma_backend_rejected_in_external_mode(keyless_settings: Settings) -> None:
    """外部模式改不了服务端后端:--vector-store chroma 直接拒绝(exit 2)。"""
    assert main(
        [
            "--dataset",
            str(DATASET_PATH),
            "--report",
            "report.md",
            "--min-score",
            "0.35",
            "--base-url",
            "http://127.0.0.1:9",
            "--embedder-label",
            "BAAI/bge-m3",
            "--vector-store",
            "chroma",
        ]
    ) == 2
