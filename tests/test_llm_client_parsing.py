"""窄接缝:统一 LLM 客户端对上游响应体的解析(假传输层,不触网)。

三类上游响应(chat / embeddings / rerank)的形状校验与用量解析:
畸形响应必须映射为 UPSTREAM_BAD_RESPONSE,而不是让异常漏到领域层。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import UpstreamBadResponseError
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse


class FakeTransport:
    """替换系统边界(外部 HTTP):只回一个预设响应。"""

    def __init__(self, response: TransportResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> TransportResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers), "timeout": timeout})
        return self._response


def build_client(settings: Settings, body: Any) -> UnifiedLLMClient:
    configured = settings.model_copy(update={"siliconflow_api_key": "sk-test-placeholder"})
    return UnifiedLLMClient(configured, transport=FakeTransport(TransportResponse(200, body)))


# ===== chat =====


async def test_chat_accepts_content_list_shape(settings):
    """部分供应商把 content 写成片段数组:按文本片段拼接,不视为畸形。"""
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "按工龄"},
                        {"type": "text", "text": "计算。"},
                    ],
                }
            }
        ]
    }
    client = build_client(settings, body)

    result = await client.chat([{"role": "user", "content": "年假怎么算?"}])

    assert result.content == "按工龄计算。"


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": 123}}]},
        {"choices": "not-a-list"},
        {},
    ],
)
async def test_chat_rejects_malformed_body(settings, body):
    client = build_client(settings, body)

    with pytest.raises(UpstreamBadResponseError):
        await client.chat([{"role": "user", "content": "年假怎么算?"}])


# ===== embeddings =====


async def test_embed_rejects_count_mismatch(settings):
    """向量数量必须与输入对齐:错位即畸形,不静默通过。"""
    body = {"data": [{"index": 0, "embedding": [0.1]}]}
    client = build_client(settings, body)

    with pytest.raises(UpstreamBadResponseError):
        await client.embed(["甲", "乙"])


async def test_embed_rejects_non_numeric_embedding(settings):
    body = {"data": [{"index": 0, "embedding": ["not-a-number"]}]}
    client = build_client(settings, body)

    with pytest.raises(UpstreamBadResponseError):
        await client.embed(["甲"])


async def test_embed_orders_by_index_ignoring_missing_index(settings):
    """缺 index 的条目按出现顺序排在有 index 的条目之前。"""
    body = {
        "data": [
            {"index": 1, "embedding": [0.2]},
            {"embedding": [0.1]},
        ]
    }
    client = build_client(settings, body)

    result = await client.embed(["甲", "乙"])

    assert result.vectors == [[0.1], [0.2]]


# ===== rerank =====


async def test_rerank_accepts_score_field_fallback(settings):
    """relevance_score 缺失时回落到 score 字段。"""
    body = {"results": [{"index": 0, "score": 0.7}]}
    client = build_client(settings, body)

    result = await client.rerank("年假怎么算?", ["甲"])

    assert [(item.index, item.score) for item in result.items] == [(0, 0.7)]


@pytest.mark.parametrize(
    "body",
    [
        {"results": "not-a-list"},
        {"results": [{"no_index": 1}]},
        {"results": [{"index": 0, "relevance_score": "not-a-number"}]},
        {},
    ],
)
async def test_rerank_rejects_malformed_body(settings, body):
    client = build_client(settings, body)

    with pytest.raises(UpstreamBadResponseError):
        await client.rerank("年假怎么算?", ["甲"])


# ===== 用量解析 =====


async def test_usage_totals_are_derived_when_total_missing(settings):
    """上游没给 total_tokens 时按 prompt + completion 补齐。"""
    body = {
        "choices": [{"message": {"role": "assistant", "content": "好"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }
    client = build_client(settings, body)

    result = await client.chat([{"role": "user", "content": "问题"}])

    assert result.usage.total_tokens == 7


async def test_usage_with_garbage_values_degrades_to_zero(settings):
    """用量字段是脏数据时按 0 计,不让上游的怪字段打挂问答主链路。"""
    body = {
        "choices": [{"message": {"role": "assistant", "content": "好"}}],
        "usage": {"prompt_tokens": "many", "completion_tokens": None},
    }
    client = build_client(settings, body)

    result = await client.chat([{"role": "user", "content": "问题"}])

    assert result.usage.prompt_tokens == 0
    assert result.usage.completion_tokens == 0
    assert result.usage.total_tokens == 0
