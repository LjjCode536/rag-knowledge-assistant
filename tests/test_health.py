"""HTTP 主接缝:健康检查必须返回统一信封,且 trace_id 全链路一致。"""

from __future__ import annotations


def test_health_returns_unified_envelope(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["message"] == "ok"
    assert body["data"] == {
        "status": "ok",
        "version": "0.1.0",
        "environment": "test",
        "upstream_configured": False,
    }
    assert body["details"] is None
    assert body["trace_id"]
    assert body["timestamp"]
    assert response.headers["X-Request-ID"] == body["trace_id"]


def test_health_reuses_caller_trace_id(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-abc-0001"})

    assert response.headers["X-Request-ID"] == "trace-abc-0001"
    assert response.json()["trace_id"] == "trace-abc-0001"


def test_health_replaces_malformed_trace_id(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})

    trace_id = response.json()["trace_id"]
    assert trace_id != "bad id with spaces"
    assert response.headers["X-Request-ID"] == trace_id


def test_health_reports_configured_upstream(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    configured = settings.model_copy(update={"siliconflow_api_key": "sk-test-placeholder"})
    with TestClient(create_app(configured)) as client:
        response = client.get("/health")

    assert response.json()["data"]["upstream_configured"] is True
