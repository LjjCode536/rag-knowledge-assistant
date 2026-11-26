"""审计事件:越权拒绝、平台管理员跨租户访问、文档授权变更与文档删除的留痕。

审计事件**只追加不修改**(ADR-0020 / ADR-0021):它是"谁在什么时候对什么
做了什么、结果如何"的事实记录,不参与业务判定。字段口径固定为
时间 / 租户 / 主体 / 动作 / 资源 / 结果 / ``trace_id`` / 细节,内存实现与
PostgreSQL 实现共享同一模型。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AuditAction(StrEnum):
    """首批审计动作;新增只追加,不改名。"""

    ACCESS_DENIED = "access.denied"
    ACCESS_CROSS_TENANT = "access.cross_tenant"
    PLATFORM_TENANT_ACCESS = "platform.tenant_access"
    DOCUMENT_GRANT = "document.grant"
    DOCUMENT_REVOKE = "document.revoke"
    DOCUMENT_DELETE = "document.delete"


class AuditResult(StrEnum):
    """一次动作的结果:成功、被拒绝、失败。"""

    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


class AuditEvent(BaseModel):
    """一条审计事件:字段即查询与展示口径,细节放 ``details`` 不侵占主字段。"""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"aud_{uuid4().hex}")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant: str = Field(min_length=1)
    principal_id: str | None = None
    action: AuditAction
    resource_type: str | None = None
    resource_id: str | None = None
    result: AuditResult
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
