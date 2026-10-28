"""HTTP 主接缝:响应头、响应体、日志共享同一 trace_id。"""

from __future__ import annotations

from tests.support import json_log_entries


def test_request_log_shares_trace_id_with_response(capsys, client):
    response = client.get("/health", headers={"X-Request-ID": "trace-log-0001"})

    entries = json_log_entries(capsys.readouterr().out)
    request_logs = [entry for entry in entries if entry.get("event") == "http_request"]

    assert request_logs
    assert request_logs[-1]["trace_id"] == "trace-log-0001"
    assert request_logs[-1]["path"] == "/health"
    assert request_logs[-1]["status_code"] == 200
    assert request_logs[-1]["duration_ms"] >= 0
    assert response.json()["trace_id"] == request_logs[-1]["trace_id"]


def test_error_log_keeps_stack_while_response_hides_it(capsys, probe_client):
    response = probe_client.get("/__probe/boom", headers={"X-Request-ID": "trace-log-0002"})

    entries = json_log_entries(capsys.readouterr().out)
    error_logs = [entry for entry in entries if entry.get("event") == "unhandled_exception"]

    assert error_logs
    assert error_logs[-1]["trace_id"] == "trace-log-0002"
    assert "secret-stack-marker" in error_logs[-1]["exception"]
    assert "secret-stack-marker" not in response.text
