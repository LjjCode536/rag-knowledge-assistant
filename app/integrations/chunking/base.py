"""切片器契约与半成品。

:class:`RawChunk` 是切片半成品:只有内容与页码 / 标题 / 来源区间等定位信息;
租户、部门等文档级元数据由服务层(:mod:`app.services.chunking`)补齐。
框架类型(langchain-text-splitters)只出现在本层,不进领域与接口层。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.domain.documents import DocumentElement


@dataclass(frozen=True, slots=True)
class RawChunk:
    """切片半成品:内容 + 定位信息。"""

    content: str
    page_number: int | None
    title: str | None
    element_index_start: int
    element_index_end: int


class Chunker(Protocol):
    """切分策略契约:输入解析元素与参数,输出去业务化的切片半成品序列。"""

    def chunk(
        self, elements: Sequence[DocumentElement], *, chunk_size: int, chunk_overlap: int
    ) -> list[RawChunk]:
        """按策略切分;内容与定位之外不做任何元数据装配。"""
        ...
