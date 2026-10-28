"""TXT 解析:按空行分段,行号作为定位元数据。"""

from __future__ import annotations

from app.domain.cleaning import TextCleaner
from app.domain.documents import DocumentElement, DocumentFormat, ElementType, ParsedDocument
from app.integrations.parsing.base import decode_utf8

_cleaner = TextCleaner()


class TxtParser:
    file_type = DocumentFormat.TXT

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        text = decode_utf8(data, file_name=file_name)
        elements: list[DocumentElement] = []
        block_lines: list[str] = []
        block_start = 1

        # 哨兵空行收尾:循环内遇到空行或结尾即结算当前段落
        for line_no, raw_line in enumerate([*text.splitlines(), ""], start=1):
            if raw_line.strip():
                if not block_lines:
                    block_start = line_no
                block_lines.append(raw_line)
                continue
            if block_lines:
                content = _cleaner.clean("\n".join(block_lines))
                if content:
                    elements.append(
                        DocumentElement(
                            element_type=ElementType.PARAGRAPH,
                            content=content,
                            order_index=len(elements),
                            line=block_start,
                        )
                    )
                block_lines = []

        return ParsedDocument(
            document_id=document_id,
            file_name=file_name,
            file_type=self.file_type,
            elements=elements,
        )
