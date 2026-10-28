"""PDF 解析:PyMuPDF 按页提取文本块,保留页码。

扫描件(无文本层)不做 OCR(项目 out of scope),显式报错带 need_ocr 提示,
避免空文档静默入库。
"""

from __future__ import annotations

import pymupdf

from app.core.errors import DocumentParseError
from app.domain.cleaning import TextCleaner
from app.domain.documents import DocumentElement, DocumentFormat, ElementType, ParsedDocument

_cleaner = TextCleaner()


class PdfParser:
    file_type = DocumentFormat.PDF

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise DocumentParseError(
                f"不是有效的 PDF 文件:{file_name}", details={"reason": str(exc)}
            ) from exc
        try:
            if document.needs_pass:
                raise DocumentParseError(f"PDF 已加密,无法解析:{file_name}")
            elements, page_count = self._extract(document)
            if not elements:
                raise DocumentParseError(
                    f"PDF 未提取到任何文本(疑似扫描件,标记 need_ocr;M1 不做 OCR):{file_name}"
                )
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(
                f"PDF 解析失败:{file_name}", details={"reason": str(exc)}
            ) from exc
        finally:
            document.close()
        return ParsedDocument(
            document_id=document_id,
            file_name=file_name,
            file_type=self.file_type,
            page_count=page_count,
            elements=elements,
        )

    def _extract(self, document: pymupdf.Document) -> tuple[list[DocumentElement], int]:
        elements: list[DocumentElement] = []
        for page_index, page in enumerate(document):
            # blocks 模式天然按版面分块:一个文本块约等于一个段落,比逐行更贴近结构
            for block in page.get_text("blocks"):
                if block[6] != 0:  # 0 = 文本块,1 = 图片块
                    continue
                content = _cleaner.clean(block[4])
                if not content:
                    continue
                elements.append(
                    DocumentElement(
                        element_type=ElementType.PARAGRAPH,
                        content=content,
                        order_index=len(elements),
                        page_number=page_index + 1,
                    )
                )
        return elements, document.page_count
