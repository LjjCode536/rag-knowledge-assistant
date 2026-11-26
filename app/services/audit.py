"""审计服务:变更类审计与业务写同事务,越权拒绝的审计尽力而为(ADR-0020)。

一次动作在审计上只有两种口径:

- **变更类**(授权、撤销、删除、平台管理员跨租户访问):审计写入失败即
  抛 :class:`AuditWriteError`,业务写不执行;
- **拒绝类**(越权 / 跨租户对象访问):审计失败只记结构化告警,不改变
  已经确定的响应(调用方看到 403 / 404,不会因审计故障变成 500)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import structlog

from app.core.errors import AuditWriteError
from app.domain.audit import AuditAction, AuditEvent, AuditResult
from app.domain.repositories import AuditRepository

logger = structlog.get_logger("audit")


class AuditService:
    """审计事件的写入与只读查询;业务层只传事实,不拼事件格式。"""

    def __init__(self, *, repository: AuditRepository) -> None:
        self._repository = repository

    def record_required(
        self,
        *,
        tenant: str,
        principal_id: str | None,
        action: AuditAction,
        result: AuditResult = AuditResult.SUCCESS,
        trace_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """变更类审计:失败即抛 500,调用方据此中止业务写。"""
        event = _event(
            tenant=tenant,
            principal_id=principal_id,
            action=action,
            result=result,
            trace_id=trace_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )
        try:
            self._repository.add(event)
        except Exception as exc:
            logger.error(
                "audit_write_failed",
                trace_id=trace_id,
                action=action.value,
                tenant=tenant,
                error=str(exc),
            )
            raise AuditWriteError(
                details={"action": action.value, "reason": str(exc)}
            ) from exc
        return event

    def record_denial(self, event: AuditEvent) -> None:
        """拒绝类审计:尽力而为,失败不改写调用方已得到的响应。"""
        try:
            self._repository.add(event)
        except Exception as exc:
            logger.warning(
                "audit_denial_write_failed",
                trace_id=event.trace_id,
                action=event.action.value,
                tenant=event.tenant,
                error=str(exc),
            )

    def list_events(
        self,
        *,
        tenant: str,
        actions: Sequence[AuditAction] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        events = self._repository.list(
            tenant=tenant,
            actions=actions,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
        total = self._repository.count(
            tenant=tenant, actions=actions, start=start, end=end
        )
        return events, total


def denial_event(
    *,
    tenant: str,
    principal_id: str | None,
    audit: dict[str, Any],
    trace_id: str | None,
) -> AuditEvent:
    """把异常上的审计线索还原成事件;调用方只在拒绝路径使用。"""
    action = audit.get("action", AuditAction.ACCESS_DENIED)
    if not isinstance(action, AuditAction):
        action = AuditAction(str(action))
    details = {
        key: value
        for key, value in audit.items()
        if key not in {"action", "resource_type", "resource_id"}
    }
    return _event(
        tenant=tenant,
        principal_id=principal_id,
        action=action,
        result=AuditResult.DENIED,
        trace_id=trace_id,
        resource_type=audit.get("resource_type"),
        resource_id=audit.get("resource_id"),
        details=details,
    )


def _event(
    *,
    tenant: str,
    principal_id: str | None,
    action: AuditAction,
    result: AuditResult,
    trace_id: str | None,
    resource_type: str | None,
    resource_id: str | None,
    details: dict[str, Any] | None,
) -> AuditEvent:
    return AuditEvent(
        tenant=tenant,
        principal_id=principal_id,
        action=action,
        result=result,
        trace_id=trace_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
    )
