"""确定性向量替身:词面哈希进 64 维,同词面 → 相同向量,重叠多 → 更相似。

不触网、不计费,供评估器在无密钥环境(CI)复现检索指标;测试也从这里注入。
它只是评估与测试的向量来源,生产检索仍由统一 LLM 客户端调用真实 embedding 模型。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from app.domain.lexical import text_tokens  # 分词口径随本替身对外共用
from app.integrations.llm.client import EmbedResult, TokenUsage

DIM = 64
# 评估基线用 1024 维:与生产 bge-m3 的向量维度对齐,同时把哈希碰撞压到可忽略;
# 测试仍用 64 维的轻量默认值,只关心相对排序,不关心维度。
EVALUATION_DIM = 1024


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
