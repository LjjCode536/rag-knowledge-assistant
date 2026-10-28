"""TextCleaner:解析产物的纯文本净化(教材第 40 章清洗规则)。

只做无损净化,不改变语义内容;页眉页脚去重等有损规则不在此实现。
"""

from __future__ import annotations

import re

# 零宽字符与 BOM:肉眼不可见却会污染切片与检索
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


class TextCleaner:
    """统一清洗规则:

    - 去除零宽字符,不间断空格归一为普通空格
    - 换行统一为 LF
    - 连续空格 / 制表符压缩为单个空格
    - 连续 3 个以上换行压缩为 2 个
    - 去除每行首尾空白
    """

    def clean(self, text: str) -> str:
        if not text:
            return ""
        text = text.translate(_ZERO_WIDTH)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\u00a0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.strip() for line in text.split("\n"))
        return text.strip()
