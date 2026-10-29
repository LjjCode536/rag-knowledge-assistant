"""四种切分策略:段落 / 标题 / 表格感知 / 元素(教材第 41 章的生产版)。

共用机械:langchain RecursiveCharacterTextSplitter(只在本层出现)+ 元素偏移映射,
让每个切片都能溯源到解析元素(页码、标题、元素区间)。

策略语义:

- paragraph:非标题元素连成整体文本流,递归切分;表格等结构可能被切断(基线)。
- heading:按标题元素分节,节标题进元数据;节内超长时再按尺寸细切。
- table_aware:表格 / 代码等结构原子不可切,连续散文合并后按尺寸切分并带重叠。
- element:一个解析元素一个切片,结构完整、无合并;标题元素自身内容保留。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.errors import DocumentChunkingError
from app.domain.documents import DocumentElement, ElementType
from app.integrations.chunking.base import RawChunk

_SEPARATORS = ("\n\n", "\n", "。", " ", "")
_ATOMIC_TYPES = frozenset({ElementType.TABLE, ElementType.CODE})
_TITLE = ElementType.TITLE

type _Positioned = tuple[int, DocumentElement]  # (elements 下标, 元素)


def _split(text: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    """递归切分:优先在段落 / 行 / 句边界断开,兜底按字符切(保证切片不超长)。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=list(_SEPARATORS),
    )
    return splitter.split_text(text)


def _title_chain(elements: Sequence[DocumentElement]) -> list[str | None]:
    """最近标题链:每个元素位置之前最近的标题文本(标题元素自身除外)。"""
    chain: list[str | None] = []
    current = None
    for element in elements:
        chain.append(current)
        if element.element_type is _TITLE:
            current = element.content
    return chain


def _concat(items: Sequence[_Positioned]) -> tuple[str, list[tuple[int, int, int]]]:
    """空行连接元素,返回 (拼接文本, [(起始偏移, 结束偏移, elements 下标), ...])。"""
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for position, element in items:
        if parts:
            parts.append("\n\n")
            cursor += 2
        spans.append((cursor, cursor + len(element.content), position))
        parts.append(element.content)
        cursor += len(element.content)
    return "".join(parts), spans


def _position_at(spans: list[tuple[int, int, int]], offset: int) -> int:
    """定位偏移所在的元素下标;落在分隔符或边界上的偏移归到前一个元素。"""
    latest = spans[0][2]
    for start, end, position in spans:
        if start <= offset < end:
            return position
        if start <= offset:
            latest = position
    return latest


def _raw_pieces(
    pieces: list[str],
    *,
    text: str,
    spans: list[tuple[int, int, int]],
    elements: Sequence[DocumentElement],
    chunk_overlap: int,
    title_for: Callable[[int], str | None],
) -> list[RawChunk]:
    """把切片文本映射回解析元素,补齐页码、标题与来源区间。

    相邻切片的重叠不超过 chunk_overlap:搜索起点用"上一片起点 + 上一片长 - 重叠"
    推进,重复文本(如表格编号)不会被错误地提前命中。
    """
    raw: list[RawChunk] = []
    search_from = 0
    for piece in pieces:
        start = text.find(piece, search_from)
        if start < 0:
            raise DocumentChunkingError(details={"reason": "切片无法对应到解析元素"})
        search_from = max(start + 1, start + len(piece) - chunk_overlap)
        start_position = _position_at(spans, start)
        end_position = _position_at(spans, start + len(piece) - 1)
        raw.append(
            RawChunk(
                content=piece,
                page_number=elements[start_position].page_number,
                title=title_for(start_position),
                element_index_start=elements[start_position].order_index,
                element_index_end=elements[end_position].order_index,
            )
        )
    return raw


class ParagraphChunker:
    """段落策略:整体文本流递归切分(基线);表格等结构可能被切断。"""

    def chunk(
        self, elements: Sequence[DocumentElement], *, chunk_size: int, chunk_overlap: int
    ) -> list[RawChunk]:
        prose = [
            (position, element)
            for position, element in enumerate(elements)
            if element.element_type is not _TITLE
        ]
        titles = _title_chain(elements)
        text, spans = _concat(prose)
        pieces = _split(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return _raw_pieces(
            pieces,
            text=text,
            spans=spans,
            elements=elements,
            chunk_overlap=chunk_overlap,
            title_for=titles.__getitem__,
        )


class HeadingChunker:
    """标题策略:按标题元素分节,节标题进元数据;节内超长时再按尺寸细切。"""

    def chunk(
        self, elements: Sequence[DocumentElement], *, chunk_size: int, chunk_overlap: int
    ) -> list[RawChunk]:
        sections: list[tuple[str | None, list[_Positioned]]] = []
        section_title: str | None = None
        current: list[_Positioned] = []
        for position, element in enumerate(elements):
            if element.element_type is _TITLE:
                if current:
                    sections.append((section_title, current))
                    current = []
                section_title = element.content
            else:
                current.append((position, element))
        if current:
            sections.append((section_title, current))

        raw: list[RawChunk] = []
        for section_title, items in sections:
            text, spans = _concat(items)
            pieces = _split(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            raw.extend(
                _raw_pieces(
                    pieces,
                    text=text,
                    spans=spans,
                    elements=elements,
                    chunk_overlap=chunk_overlap,
                    title_for=self._constant_title(section_title),
                )
            )
        return raw

    @staticmethod
    def _constant_title(section_title: str | None) -> Callable[[int], str | None]:
        """该节所有切片共享同一个标题元数据。"""
        return lambda _position: section_title


class TableAwareChunker:
    """表格感知策略:表格 / 代码原子不可切,连续散文合并后按尺寸切分并带重叠。"""

    def chunk(
        self, elements: Sequence[DocumentElement], *, chunk_size: int, chunk_overlap: int
    ) -> list[RawChunk]:
        titles = _title_chain(elements)
        raw: list[RawChunk] = []
        run: list[_Positioned] = []

        def flush_run() -> None:
            if not run:
                return
            text, spans = _concat(run)
            pieces = _split(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            raw.extend(
                _raw_pieces(
                    pieces,
                    text=text,
                    spans=spans,
                    elements=elements,
                    chunk_overlap=chunk_overlap,
                    title_for=titles.__getitem__,
                )
            )
            run.clear()

        for position, element in enumerate(elements):
            if element.element_type is _TITLE:
                flush_run()  # 标题只更新"最近标题",不产生切片内容
            elif element.element_type in _ATOMIC_TYPES:
                flush_run()
                raw.append(
                    RawChunk(
                        content=element.content,
                        page_number=element.page_number,
                        title=titles[position],
                        # 原子结构:来源区间就是单个元素,永远整块出现
                        element_index_start=element.order_index,
                        element_index_end=element.order_index,
                    )
                )
            else:
                run.append((position, element))
        flush_run()
        return raw


class ElementChunker:
    """元素策略:一个解析元素一个切片,结构完整、无合并;标题元素自身内容保留。"""

    def chunk(
        self, elements: Sequence[DocumentElement], *, chunk_size: int, chunk_overlap: int
    ) -> list[RawChunk]:
        titles = _title_chain(elements)
        return [
            RawChunk(
                content=element.content,
                page_number=element.page_number,
                title=element.content if element.element_type is _TITLE else titles[position],
                # 尺寸 / 重叠参数对元素策略不适用:结构保真优先
                element_index_start=element.order_index,
                element_index_end=element.order_index,
            )
            for position, element in enumerate(elements)
        ]
