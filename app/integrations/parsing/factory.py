"""解析器工厂:按格式分发(教材第 40 章 ParserFactory 的生产版)。"""

from __future__ import annotations

from app.core.errors import UnsupportedFormatError
from app.domain.documents import DocumentFormat
from app.integrations.parsing.base import DocumentParser
from app.integrations.parsing.docx_parser import DocxParser
from app.integrations.parsing.html_parser import HtmlParser
from app.integrations.parsing.markdown_parser import MarkdownParser
from app.integrations.parsing.pdf_parser import PdfParser
from app.integrations.parsing.txt_parser import TxtParser

_PARSERS: dict[DocumentFormat, DocumentParser] = {
    DocumentFormat.TXT: TxtParser(),
    DocumentFormat.MD: MarkdownParser(),
    DocumentFormat.HTML: HtmlParser(),
    DocumentFormat.DOCX: DocxParser(),
    DocumentFormat.PDF: PdfParser(),
}


def get_parser(file_type: DocumentFormat) -> DocumentParser:
    parser = _PARSERS.get(file_type)
    if parser is None:  # pragma: no cover — DocumentFormat 枚举齐全时不可达
        raise UnsupportedFormatError(details={"file_type": file_type.value})
    return parser
