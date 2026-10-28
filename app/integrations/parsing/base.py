"""解析器契约与文本类格式共用工具。"""

from __future__ import annotations

from typing import Protocol

from app.core.errors import DocumentParseError
from app.domain.documents import DocumentFormat, ParsedDocument


class DocumentParser(Protocol):
    """单一格式解析器:原始字节进,ParsedDocument 出,无其他副作用。"""

    file_type: DocumentFormat

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        """把文件内容解析为结构化元素;内容不合法抛 DocumentParseError。"""
        ...


def decode_utf8(data: bytes, *, file_name: str) -> str:
    """文本类格式统一按 UTF-8 解码;失败显式报错,不静默吞错。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(
            f"文件不是有效的 UTF-8 文本:{file_name}", details={"reason": str(exc)}
        ) from exc
