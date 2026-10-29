"""切片领域的模型:策略、切片与其质量告警(教材第 41 章的生产版)。

切片是切分链路的唯一产物:服务层负责把解析元素切分并补齐租户等部门元数据;
检索层(M4+)只依赖本层结构,不关心切分策略如何实现。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ChunkingStrategy(StrEnum):
    """切分策略。

    - paragraph:整体文本流切分(基线),表格等结构可能被切断
    - heading:按标题分节,节内超长再细切
    - table_aware:表格 / 代码等结构原子不可切,连续散文合并后按尺寸切分
    - element:一个解析元素一个切片(最大粒度保真)
    """

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE_AWARE = "table_aware"
    ELEMENT = "element"


class ChunkWarningCode(StrEnum):
    """切片质量告警码:告警可见但不视为失败。"""

    CHUNK_TOO_SHORT = "CHUNK_TOO_SHORT"
    CHUNK_TOO_LONG = "CHUNK_TOO_LONG"


class ChunkQualityWarning(BaseModel):
    """切片质量告警:稳定码 + 文案 + 定位详情。"""

    code: ChunkWarningCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """切片:最小检索单元,携带切片元数据与页码、标题等定位信息。

    可溯源:document_id 指向解析文档,element_index_* 圈出元素的来源区间。
    """

    chunk_id: str = Field(default_factory=lambda: uuid4().hex)
    document_id: str
    index: int
    content: str
    # ===== 切片元数据 =====
    tenant: str
    department: str | None = None
    doc_type: str | None = None
    version: str
    # ===== 定位信息 =====
    page_number: int | None = None
    title: str | None = None
    element_index_start: int
    element_index_end: int
