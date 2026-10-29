"""确定性假向量替身:词面哈希进 64 维,同词面 → 相同向量,重叠多 → 更相似。

不触网、不计费,供 API 主接缝的所有向量化 / 检索测试注入 create_app。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from app.integrations.llm.client import EmbedResult, TokenUsage

DIM = 64
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


def text_vector(text: str, dim: int = DIM) -> list[float]:
    vector = [0.0] * dim
    for token in text_tokens(text):
        digest = hashlib.md5(token.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:2], "big") % dim] += 1.0
    return vector


class DeterministicEmbedder:
    """统一客户端 embed 接缝的替身:签名与 UnifiedLLMClient.embed 一致。"""

    def __init__(self, *, model: str = "BAAI/bge-m3", dim: int = DIM) -> None:
        self._model = model
        self._dim = dim

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> EmbedResult:
        vectors = [text_vector(text, self._dim) for text in texts]
        prompt_tokens = sum(len(text) for text in texts)
        return EmbedResult(
            vectors=vectors,
            model=model or self._model,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                total_tokens=prompt_tokens,
            ),
            latency_ms=1.23,
        )
