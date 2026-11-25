"""解析层行为:Markdown token 映射与 HTML 噪声边界(ADR-0017)。

这些用例直接调解析器,锁住元素类型、内容、行号与表格完整性;
跨层契约(上传 → 状态机 → elements 接口)由 test_documents_api.py 覆盖。
"""

from __future__ import annotations

from app.domain.documents import DocumentElement
from app.integrations.parsing.html_parser import HtmlParser
from app.integrations.parsing.markdown_parser import MarkdownParser


def _parse_markdown(text: str) -> list[DocumentElement]:
    parsed = MarkdownParser().parse(
        text.encode(), document_id="doc", file_name="guide.md"
    )
    return parsed.elements


def _parse_html(html: str) -> list[DocumentElement]:
    parsed = HtmlParser().parse(html.encode(), document_id="doc", file_name="page.html")
    return parsed.elements


def _types(elements: list[DocumentElement]) -> list[str]:
    return [element.element_type.value for element in elements]


def test_markdown_keeps_list_markers_and_document_order():
    markdown = "# 清单\n\n- 第一步\n  - 子步骤\n1. 第一条\n2. 第二条\n"

    elements = _parse_markdown(markdown)

    assert _types(elements) == ["title", "list", "list", "list", "list"]
    assert [element.content for element in elements] == [
        "清单",
        "- 第一步",
        "- 子步骤",
        "1. 第一条",
        "2. 第二条",
    ]
    assert [element.line for element in elements] == [1, 3, 4, 5, 6]


def test_markdown_ordered_list_numbers_follow_start_value():
    markdown = "3. 第三条\n4. 第四条\n"

    elements = _parse_markdown(markdown)

    assert [element.content for element in elements] == ["3. 第三条", "4. 第四条"]


def test_markdown_setext_heading_and_pipe_less_table():
    markdown = "条款\n===\n\n参数 | 值\n--- | ---\n超时 | 30\n"

    elements = _parse_markdown(markdown)

    assert _types(elements) == ["title", "table"]
    assert elements[0].heading_level == 1
    assert elements[0].line == 1
    assert elements[1].content == "参数 | 值\n超时 | 30"
    assert elements[1].line == 4


def test_markdown_blockquote_becomes_paragraph_without_marker():
    elements = _parse_markdown("> 注意:逾期不退\n")

    assert _types(elements) == ["paragraph"]
    assert elements[0].content == "注意:逾期不退"


def test_markdown_inline_markup_and_links_stay_raw():
    elements = _parse_markdown("段落 **粗体** [链接](http://example.com)\n")

    assert elements[0].content == "段落 **粗体** [链接](http://example.com)"


def test_markdown_html_block_is_kept_as_paragraph():
    elements = _parse_markdown("<div>原文</div>\n")

    assert _types(elements) == ["paragraph"]
    assert elements[0].content == "<div>原文</div>"


def test_markdown_code_block_keeps_indentation_and_splits_from_list_item():
    markdown = "- 示例\n  ```\n    echo hi\n  ```\n"

    elements = _parse_markdown(markdown)

    assert _types(elements) == ["list", "code"]
    assert elements[0].content == "- 示例"
    assert elements[1].content == "  echo hi"


def test_html_skips_page_chrome_but_keeps_article_header():
    html = (
        "<html><body>"
        "<header><p>站点导航</p></header>"
        "<nav><a href='/'>首页</a></nav>"
        "<article><header><h1>退费政策</h1></header><p>7 天内可退。</p></article>"
        "<iframe src='/ad'></iframe><svg><text>图标</text></svg>"
        "<form><input value='x'/><button>提交</button></form>"
        "<footer><p>版权所有</p></footer>"
        "</body></html>"
    )

    elements = _parse_html(html)

    assert _types(elements) == ["title", "paragraph"]
    assert [element.content for element in elements] == ["退费政策", "7 天内可退。"]
    joined = "\n".join(element.content for element in elements)
    for noise in ("站点导航", "首页", "图标", "提交", "版权所有"):
        assert noise not in joined
