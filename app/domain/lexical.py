"""词法分词:英文取词、中文二字滑窗。

混合检索关键词通道与确定性向量替身共用同一口径,保证「词面交集」
在两条通道上可比较;改分词规则即同时改变两条通道的召回语义。
"""

from __future__ import annotations

import re

_LATIN = re.compile(r"[A-Za-z0-9]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def text_tokens(text: str) -> list[str]:
    """英文取词、中文取二字滑窗,保证中文查询与切片有词面交集可言。"""
    tokens = _LATIN.findall(text)
    for run in _CJK_RUN.findall(text):
        if len(run) < 2:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens
