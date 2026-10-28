"""测试辅助:手工构造最小合法 PDF(供 PDF 解析测试,无需重型 PDF 生成依赖)。"""

from __future__ import annotations


def build_pdf(page_texts: list[str]) -> bytes:
    """构造包含若干页的简单 PDF;每页一行 ASCII 文本(Helvetica)。"""
    # 对象布局:1 = Catalog,2 = Pages,3 = Font,之后每页一对(Page, Content)
    page_ids = [4 + index * 2 for index in range(len(page_texts))]
    content_ids = [page_id + 1 for page_id in page_ids]

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{page_id} 0 R" for page_id in page_ids)
            + f"] /Count {len(page_texts)} >>"
        ).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, text in enumerate(page_texts):
        objects[page_ids[index]] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            " /Resources << /Font << /F1 3 0 R >> >>"
            f" /Contents {content_ids[index]} 0 R >>"
        ).encode()
        stream = f"BT /F1 12 Tf 72 720 Td ({_escape(text)}) Tj ET".encode()
        objects[content_ids[index]] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    start_xref = len(out)
    max_number = max(objects)
    out += f"xref\n0 {max_number + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for number in range(1, max_number + 1):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {max_number + 1} /Root 1 0 R >>\n"
        f"startxref\n{start_xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
