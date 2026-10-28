"""HTTP 主接缝:所有错误路径都收敛为统一错误码信封,且不泄漏堆栈。"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("method", "path", "status_code", "code"),
    [
        ("get", "/no-such-route", 404, "NOT_FOUND"),
        ("post", "/health", 405, "METHOD_NOT_ALLOWED"),
    ],
)
def test_routing_errors_use_unified_envelope(client, method, path, status_code, code):
    response = getattr(client, method)(path)

    assert response.status_code == status_code
    body = response.json()
    assert body["code"] == code
    assert body["data"] is None
    assert body["trace_id"] == response.headers["X-Request-ID"]


def test_request_validation_error_uses_unified_envelope(probe_client):
    response = probe_client.get("/__probe/validated")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["loc"] == ["query", "q"]
    assert body["trace_id"] == response.headers["X-Request-ID"]


def test_business_error_keeps_code_and_message(probe_client):
    response = probe_client.get("/__probe/business")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "知识库文档不存在"
    assert body["trace_id"] == response.headers["X-Request-ID"]


def test_unexpected_error_hides_stack_trace(probe_client):
    response = probe_client.get("/__probe/boom", headers={"X-Request-ID": "trace-boom-0001"})

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "服务内部错误"
    assert body["details"] is None
    assert body["trace_id"] == "trace-boom-0001"
    assert response.headers["X-Request-ID"] == "trace-boom-0001"
    assert "secret-stack-marker" not in response.text
    assert "Traceback" not in response.text
