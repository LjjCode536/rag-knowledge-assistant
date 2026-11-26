"""HTTP 主接缝:健康检查必须返回统一信封,且 trace_id 全链路一致。

``/health`` 是公开面,只留存活与粗粒度依赖;缓存与向量身份等诊断信息移到
管理员端点(ADR-0020)。
"""

from __future__ import annotations

from tests.support import ADMIN, authorized_client


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


def test_health_does_not_expose_diagnostics(client):
    data = client.get("/health").json()["data"]

    assert "qa_cache" not in data
    assert "upstream" not in data
    assert "embedding" not in data


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

    from app.main import create_app

    configured = settings.model_copy(update={"siliconflow_api_key": "sk-test-placeholder"})
    with authorized_client(create_app(configured)) as client:
        response = client.get("/health")

    assert response.json()["data"]["upstream_configured"] is True


def test_diagnostics_report_cache_stats_from_answered_traffic(settings, upload_dir):

    from app.integrations.llm.deterministic import DeterministicEmbedder
    from app.main import create_app

    app = create_app(settings, embedder=DeterministicEmbedder())
    with authorized_client(app) as client:
        client.post("/api/v1/chat", json={"question": "入职多久可以申请年假?"})
        client.post("/api/v1/chat", json={"question": "入职多久可以申请年假?"})

    with authorized_client(app, ADMIN.principal) as admin:
        data = admin.get("/api/v1/admin/diagnostics").json()["data"]

    assert data["qa_cache"] == {
        "enabled": True,
        "hits": 1,
        "misses": 1,
        "hit_rate": 0.5,
    }
    assert data["embedding"]["model"]
    assert data["upstream"] == {"circuit_breaker": "closed"}


def test_diagnostics_report_cache_disabled(settings, upload_dir):

    from app.main import create_app

    configured = settings.model_copy(update={"qa_cache_enabled": False})
    app = create_app(configured)
    with authorized_client(app, ADMIN.principal) as admin:
        data = admin.get("/api/v1/admin/diagnostics").json()["data"]

    assert data["qa_cache"] == {
        "enabled": False,
        "hits": 0,
        "misses": 0,
        "hit_rate": None,
    }


def test_diagnostics_are_admin_only(client, anonymous_client):
    assert anonymous_client.get("/api/v1/admin/diagnostics").status_code == 401
    assert client.get("/api/v1/admin/diagnostics").status_code == 403
