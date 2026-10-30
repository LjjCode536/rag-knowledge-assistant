"""确定性向量替身:词面哈希进 64 维,同词面 → 相同向量,重叠多 → 更相似。

不触网、不计费,供评估器在无密钥环境(CI)复现检索指标;测试也从这里注入。
它只是评估与测试的向量来源,生产检索仍由统一 LLM 客户端调用真实 embedding 模型。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from app.integrations.llm.client import EmbedResult, TokenUsage

DIM = 64
# 评估基线用 1024 维:与生产 bge-m3 的向量维度对齐,同时把哈希碰撞压到可忽略;
# 测试仍用 64 维的轻量默认值,只关心相对排序,不关心维度。
EVALUATION_DIM = 1024
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
