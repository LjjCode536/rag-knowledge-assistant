"""文档授权:允许某授权主体读取一份逻辑文档的许可(ADR-0021)。

授权**只做允许**,没有拒绝条目;撤销即删除记录。授权挂在逻辑文档
(租户 + 文件名)上,版本退位后继续有效;文档删除是终态,连带删除授权。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GrantSubjectType(StrEnum):
    """授权主体三类:用户、部门、整个租户。"""

    USER = "user"
    DEPARTMENT = "department"
    TENANT = "tenant"


class DocumentGrant(BaseModel):
    """一条文档授权记录:以逻辑文档(租户 + 文件名)为键。"""

    model_config = ConfigDict(frozen=True)

    grant_id: str = Field(default_factory=lambda: f"grt_{uuid4().hex}")
    tenant: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    subject_type: GrantSubjectType
    # 租户级授权的 subject_id 为空:它天然指向授权记录所在租户
    subject_id: str | None = None
    granted_by: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _subject_matches_type(self) -> DocumentGrant:
        subject = (self.subject_id or "").strip()
        if self.subject_type is GrantSubjectType.TENANT:
            if subject:
                raise ValueError("租户级授权不应携带 subject_id")
        elif not subject:
            raise ValueError("用户 / 部门授权必须给出 subject_id")
        return self
