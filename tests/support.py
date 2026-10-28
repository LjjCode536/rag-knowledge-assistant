"""测试辅助:解析 structlog JSON 输出。"""

from __future__ import annotations

import json


def json_log_entries(output: str) -> list[dict]:
    entries: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            entries.append(json.loads(line))
    return entries
