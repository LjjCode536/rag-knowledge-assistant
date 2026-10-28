"""HTML 解析:BeautifulSoup DOM 游走,过滤噪声标签,保留正文结构。"""

from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag

from app.domain.cleaning import TextCleaner
from app.domain.documents import DocumentElement, DocumentFormat, ElementType, ParsedDocument

_cleaner = TextCleaner()

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head", "link", "meta"})
_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class HtmlParser:
    file_type = DocumentFormat.HTML

    def parse(self, data: bytes, *, document_id: str, file_name: str) -> ParsedDocument:
        # 传原始字节,让 BeautifulSoup 自行处理 meta charset
        soup = BeautifulSoup(data, "lxml")
        root = soup.body or soup
        elements: list[DocumentElement] = []
        self._walk(root, elements)
        return ParsedDocument(
            document_id=document_id,
            file_name=file_name,
            file_type=self.file_type,
            elements=elements,
        )

    def _walk(self, node: Tag, elements: list[DocumentElement]) -> None:
        """按文档序游走:块级标签产出元素,散落文本归拢成段落。"""
        stray: list[str] = []
        for child in node.children:
            if isinstance(child, NavigableString):
                if text := str(child).strip():
                    stray.append(text)
                continue
            if not isinstance(child, Tag):
                continue
            name = (child.name or "").lower()
            if name in _SKIP_TAGS:
                continue
            if name in _HEADING_LEVELS:
                self._flush_stray(stray, elements)
                level = _HEADING_LEVELS[name]
                self._append(elements, ElementType.TITLE, child, heading_level=level)
                continue
            if name == "p":
                self._flush_stray(stray, elements)
                self._append(elements, ElementType.PARAGRAPH, child)
                continue
            if name in ("ul", "ol"):
                self._flush_stray(stray, elements)
                self._walk_list(child, elements)
                continue
            if name == "table":
                self._flush_stray(stray, elements)
                self._take_table(child, elements)
                continue
            if name == "pre":
                self._flush_stray(stray, elements)
                self._take_code(child, elements)
                continue
            if name in ("br", "hr"):
                continue
            self._walk(child, elements)
        self._flush_stray(stray, elements)

    def _walk_list(self, node: Tag, elements: list[DocumentElement]) -> None:
        for li in node.find_all("li", recursive=False):
            nested = li.find_all(["ul", "ol"], recursive=False)
            parts = [
                child.get_text(" ", strip=True) if isinstance(child, Tag) else str(child).strip()
                for child in li.children
                if not (isinstance(child, Tag) and (child.name or "").lower() in ("ul", "ol"))
            ]
            content = _cleaner.clean(" ".join(part for part in parts if part))
            if content:
                elements.append(
                    DocumentElement(
                        element_type=ElementType.LIST,
                        content=content,
                        order_index=len(elements),
                    )
                )
            for sub in nested:
                self._walk_list(sub, elements)

    def _take_table(self, table: Tag, elements: list[DocumentElement]) -> None:
        rows = [
            " | ".join(
                cell
                for cell in (c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"]))
                if cell
            )
            for tr in table.find_all("tr")
        ]
        content = _cleaner.clean("\n".join(row for row in rows if row))
        if content:
            elements.append(
                DocumentElement(
                    element_type=ElementType.TABLE, content=content, order_index=len(elements)
                )
            )

    def _take_code(self, pre: Tag, elements: list[DocumentElement]) -> None:
        content = pre.get_text().strip()
        if content:
            elements.append(
                DocumentElement(
                    element_type=ElementType.CODE, content=content, order_index=len(elements)
                )
            )

    def _flush_stray(self, stray: list[str], elements: list[DocumentElement]) -> None:
        content = _cleaner.clean(" ".join(stray))
        stray.clear()
        if content:
            elements.append(
                DocumentElement(
                    element_type=ElementType.PARAGRAPH, content=content, order_index=len(elements)
                )
            )

    def _append(
        self,
        elements: list[DocumentElement],
        element_type: ElementType,
        tag: Tag,
        *,
        heading_level: int | None = None,
    ) -> None:
        content = _cleaner.clean(tag.get_text(" ", strip=True))
        if not content:
            return
        elements.append(
            DocumentElement(
                element_type=element_type,
                content=content,
                order_index=len(elements),
                heading_level=heading_level,
            )
        )
