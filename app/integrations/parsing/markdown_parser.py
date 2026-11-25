"""Markdown 解析:markdown-it-py 的 token 流映射为解析元素(ADR-0017)。

结构映射:

- 标题(ATX / Setext)→ TITLE,层级取 h1-h6;行号取 token.map
- 段落 / 引用块 → PARAGRAPH;引用符号 ``>`` 由 token 边界消化,不进正文
- 列表项 → 每项一个 LIST 元素,content 保留标记(``- `` / 按 start 重算的 ``N. ``);
  嵌套项按文档序展开为独立元素,不建模层级
- 表格(GFM,含无首尾竖线写法)→ 单个 TABLE 元素,单元格以 `` | `` 连接
- 围栏 / 缩进代码块 → CODE 元素,原样保留缩进
- HTML 块 → 原样作为 PARAGRAPH,不递归解析(嵌套 HTML 超出解析层职责)

行内 Markdown 标记保持原样(链接 URL 因此保留在正文里);行号统一为 1 基。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.domain.cleaning import TextCleaner
from app.domain.documents import DocumentElement, DocumentFormat, ElementType, ParsedDocument
from app.integrations.parsing.base import decode_utf8

_cleaner = TextCleaner()
# commonmark 预设不含表格;项目需要 GFM 表格,单独启用 table 规则
_parser = MarkdownIt("commonmark").enable("table")


@dataclass(slots=True)
class _ListContext:
    """一层列表(无序 / 有序)的编号状态。"""

    ordered: bool
    start: int = 1
    next_index: int = 0


@dataclass(slots=True)
class _ItemFrame:
    """正在收集的列表项;记录插入位置,保证父项排在子项之前。"""

    marker: str
    line: int | None
    insert_at: int
    lines: list[str] = field(default_factory=list)


class MarkdownParser:
    file_type = DocumentFormat.MD

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        text = decode_utf8(data, file_name=file_name)
        elements = self._extract_elements(_parser.parse(text))
        # 插入父列表项会打乱产出顺序里的序号,统一按最终次序连续编号
        for index, element in enumerate(elements):
            element.order_index = index
        return ParsedDocument(
            document_id=document_id,
            file_name=file_name,
            file_type=self.file_type,
            elements=elements,
        )

    @staticmethod
    def _extract_elements(tokens: list[Token]) -> list[DocumentElement]:
        elements: list[DocumentElement] = []
        lists: list[_ListContext] = []
        items: list[_ItemFrame] = []
        heading_level: int | None = None

        index = 0
        while index < len(tokens):
            token = tokens[index]
            kind = token.type

            if kind == "table_open":
                table, index = _take_table(tokens, index)
                if table is not None:
                    elements.append(table)
                continue
            if kind == "heading_open":
                heading_level = int(token.tag[1])
            elif kind == "heading_close":
                heading_level = None
            elif kind == "bullet_list_open":
                lists.append(_ListContext(ordered=False))
            elif kind == "ordered_list_open":
                start = token.attrGet("start")
                lists.append(_ListContext(ordered=True, start=int(start) if start else 1))
            elif kind in ("bullet_list_close", "ordered_list_close"):
                lists.pop()
            elif kind == "list_item_open":
                context = lists[-1]
                items.append(
                    _ItemFrame(
                        marker=_marker(context),
                        line=_line(token),
                        # 父项的插入点先于子项与项内代码块,收尾时回插即可还原文档序
                        insert_at=len(elements),
                    )
                )
                context.next_index += 1
            elif kind == "list_item_close":
                frame = items.pop()
                content = _cleaner.clean("\n".join(frame.lines))
                if content:
                    elements.insert(
                        frame.insert_at,
                        _element(ElementType.LIST, f"{frame.marker}{content}", frame.line),
                    )
            elif kind in ("fence", "code_block"):
                # 代码块即使位于列表项内也单独成元素:切片器按元素类型识别原子结构
                content = token.content.strip("\n")
                if content.strip():
                    elements.append(_element(ElementType.CODE, content, _line(token)))
            elif kind == "html_block":
                content = _cleaner.clean(token.content)
                if content:
                    if items:
                        items[-1].lines.append(content)
                    else:
                        elements.append(_element(ElementType.PARAGRAPH, content, _line(token)))
            elif kind == "inline":
                content = _cleaner.clean(token.content)
                if not content:
                    pass
                elif heading_level is not None:
                    elements.append(
                        _element(ElementType.TITLE, content, _line(token), heading_level)
                    )
                elif items:
                    items[-1].lines.append(content)
                else:
                    elements.append(_element(ElementType.PARAGRAPH, content, _line(token)))
            index += 1
        return elements


def _marker(context: _ListContext) -> str:
    """无序列表归一为 ``- ``;有序列表按 start + 已产出项数重算 ``N. ``。"""
    if context.ordered:
        return f"{context.start + context.next_index}. "
    return "- "


def _line(token: Token) -> int | None:
    return token.map[0] + 1 if token.map else None


def _element(
    element_type: ElementType,
    content: str,
    line: int | None,
    heading_level: int | None = None,
) -> DocumentElement:
    return DocumentElement(
        element_type=element_type,
        content=content,
        line=line,
        heading_level=heading_level,
    )


def _take_table(tokens: list[Token], start: int) -> tuple[DocumentElement | None, int]:
    """吞掉整张表,返回 (表格元素, 表格之后的位置)。"""
    header = tokens[start]
    rows: list[str] = []
    cells: list[str] = []
    in_cell = False
    index = start + 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_close":
            break
        if token.type == "tr_open":
            cells = []
        elif token.type in ("th_open", "td_open"):
            in_cell = True
        elif token.type in ("th_close", "td_close"):
            in_cell = False
        elif token.type == "inline" and in_cell:
            cells.append(_cleaner.clean(token.content))
        elif token.type == "tr_close":
            row = " | ".join(cells)
            if row.strip(" |"):
                rows.append(row)
        index += 1
    content = "\n".join(rows)
    if not content:
        return None, index + 1
    return _element(ElementType.TABLE, content, _line(header)), index + 1
