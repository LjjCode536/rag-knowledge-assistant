"""DOCX 解析:python-docx 按文档顺序提取段落(标题 / 列表)与表格。

Word 表格并非规则矩阵(合并单元格等),M1 按"单元格文本用 | 连接"拍平;
DOCX 无分页概念,定位元数据用文档序(order_index)承担。
"""

from __future__ import annotations

import re
from io import BytesIO

from docx import Document as open_docx
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.errors import DocumentParseError
from app.domain.cleaning import TextCleaner
from app.domain.documents import DocumentElement, DocumentFormat, ElementType, ParsedDocument

_cleaner = TextCleaner()

_HEADING_STYLE = re.compile(r"^(?:Heading|标题)\s*(\d)$")


class DocxParser:
    file_type = DocumentFormat.DOCX

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        try:
            document = open_docx(BytesIO(data))
            elements = self._extract(document)
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(
                f"不是有效的 DOCX 文件:{file_name}", details={"reason": str(exc)}
            ) from exc
        return ParsedDocument(
            document_id=document_id,
            file_name=file_name,
            file_type=self.file_type,
            elements=elements,
        )

    def _extract(self, document: DocumentObject) -> list[DocumentElement]:
        elements: list[DocumentElement] = []
        for block in self._iter_blocks(document):
            if isinstance(block, Table):
                self._take_table(block, elements)
            else:
                self._take_paragraph(block, elements)
        return elements

    def _iter_blocks(self, document: DocumentObject):
        """按文档顺序产出段落与表格(python-docx 默认拆成两个列表,丢顺序)。"""
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, document)
            elif child.tag == qn("w:tbl"):
                yield Table(child, document)

    def _take_paragraph(self, paragraph: Paragraph, elements: list[DocumentElement]) -> None:
        content = _cleaner.clean(paragraph.text)
        if not content:
            return
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if level := _heading_level(style_name):
            elements.append(
                DocumentElement(
                    element_type=ElementType.TITLE,
                    content=content,
                    order_index=len(elements),
                    heading_level=level,
                )
            )
        elif "list" in style_name.lower():
            elements.append(
                DocumentElement(
                    element_type=ElementType.LIST, content=content, order_index=len(elements)
                )
            )
        else:
            elements.append(
                DocumentElement(
                    element_type=ElementType.PARAGRAPH, content=content, order_index=len(elements)
                )
            )

    def _take_table(self, table: Table, elements: list[DocumentElement]) -> None:
        rows = [
            " | ".join(cell.strip() for cell in (c.text for c in row.cells))
            for row in table.rows
        ]
        content = _cleaner.clean("\n".join(row for row in rows if row.strip(" |")))
        if content:
            elements.append(
                DocumentElement(
                    element_type=ElementType.TABLE, content=content, order_index=len(elements)
                )
            )


def _heading_level(style_name: str) -> int | None:
    if match := _HEADING_STYLE.match(style_name.strip()):
        return int(match.group(1))
    return None
