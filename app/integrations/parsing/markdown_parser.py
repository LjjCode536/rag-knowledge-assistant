"""Markdown 解析:保留标题 / 列表 / 表格 / 代码块结构,行号定位。"""

from __future__ import annotations

import re

from app.domain.cleaning import TextCleaner
from app.domain.documents import DocumentElement, DocumentFormat, ElementType, ParsedDocument
from app.integrations.parsing.base import decode_utf8

_cleaner = TextCleaner()

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?(\s*:?-{1,}:?\s*\|)+\s*:?-{1,}:?\s*\|?\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BLOCKQUOTE = re.compile(r"^\s*>\s?")


class MarkdownParser:
    file_type = DocumentFormat.MD

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        text = decode_utf8(data, file_name=file_name)
        elements: list[DocumentElement] = self._extract(text.splitlines())
        return ParsedDocument(
            document_id=document_id,
            file_name=file_name,
            file_type=self.file_type,
            elements=elements,
        )

    def _extract(self, lines: list[str]) -> list[DocumentElement]:
        elements: list[DocumentElement] = []
        paragraph: list[str] = []
        paragraph_start = 1

        def flush_paragraph() -> None:
            if not paragraph:
                return
            content = _cleaner.clean("\n".join(paragraph))
            if content:
                elements.append(
                    DocumentElement(
                        element_type=ElementType.PARAGRAPH,
                        content=content,
                        order_index=len(elements),
                        line=paragraph_start,
                    )
                )
            paragraph.clear()

        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if not stripped:
                flush_paragraph()
                i += 1
                continue
            if fence := _FENCE.match(raw):
                flush_paragraph()
                i = self._take_code_block(lines, i, fence.group(1), elements)
                continue
            if heading := _HEADING.match(stripped):
                flush_paragraph()
                content = _cleaner.clean(heading.group(2))
                if content:
                    elements.append(
                        DocumentElement(
                            element_type=ElementType.TITLE,
                            content=content,
                            order_index=len(elements),
                            heading_level=len(heading.group(1)),
                            line=i + 1,
                        )
                    )
                i += 1
                continue
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if _TABLE_ROW.match(raw) and _TABLE_SEPARATOR.match(next_line):
                flush_paragraph()
                i = self._take_table(lines, i, elements)
                continue
            if item := _LIST_ITEM.match(raw):
                flush_paragraph()
                content = _cleaner.clean(item.group(1))
                if content:
                    elements.append(
                        DocumentElement(
                            element_type=ElementType.LIST,
                            content=content,
                            order_index=len(elements),
                            line=i + 1,
                        )
                    )
                i += 1
                continue
            if not paragraph:
                paragraph_start = i + 1
            paragraph.append(_BLOCKQUOTE.sub("", raw))
            i += 1

        flush_paragraph()
        return elements

    def _take_code_block(
        self, lines: list[str], start: int, marker: str, elements: list[DocumentElement]
    ) -> int:
        """吞掉整个围栏代码块,返回结束围栏之后的位置。"""
        code_lines: list[str] = []
        i = start + 1
        while i < len(lines) and not lines[i].strip().startswith(marker):
            code_lines.append(lines[i])
            i += 1
        content = "\n".join(code_lines).strip()
        if content:
            elements.append(
                DocumentElement(
                    element_type=ElementType.CODE,
                    content=content,
                    order_index=len(elements),
                    line=start + 2,
                )
            )
        return i + 1

    def _take_table(self, lines: list[str], start: int, elements: list[DocumentElement]) -> int:
        """吞掉表头 + 分隔行 + 连续数据行,返回表格之后的位置。"""
        rows = [lines[start].strip()]
        i = start + 2  # 跳过分隔行
        while i < len(lines) and _TABLE_ROW.match(lines[i]):
            rows.append(lines[i].strip())
            i += 1
        content = _cleaner.clean("\n".join(self._cells(row) for row in rows))
        if content:
            elements.append(
                DocumentElement(
                    element_type=ElementType.TABLE,
                    content=content,
                    order_index=len(elements),
                    line=start + 1,
                )
            )
        return i

    @staticmethod
    def _cells(row: str) -> str:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        return " | ".join(cells)
