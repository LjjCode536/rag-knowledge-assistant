"""CLI 主缝:一条命令在无密钥环境产出 Markdown + JSON 报告。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate import main

DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "golden_dataset.yaml"


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
