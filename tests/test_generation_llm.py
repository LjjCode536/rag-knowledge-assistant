"""真实 LLM 生成器缝:有据回答 / 不足拒答 / 上游错误上抛(FakeTransport,不触网)。

验收口径(T08):
- Prompt 强约束:只依据编号资料回答、不足即统一拒答、绝不编造;
- 编号资料进入 Prompt 与上下文文本一一对应;Token 用量随结果带回;
- 上下文为空一律拒答且不触上游;上游错误(超时 / 限流)原样上抛统一错误。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import (
    UpstreamRateLimitedError,
    UpstreamTimeoutError,
)
from app.domain.qa import REFUSAL_ANSWER, AnswerContext, Citation, ContextSource
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse
from app.services.generation import SiliconFlowAnswerGenerator
from tests.test_llm_client import FakeTransport

ANSWER_BODY = {
    "choices": [
        {"message": {"role": "assistant", "content": "根据 [1],入职满一年后可以申请年假。"}}
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 15, "total_tokens": 135},
}

REFUSAL_BODY = {
    "choices": [{"message": {"role": "assistant", "content": REFUSAL_ANSWER}}],
    "usage": {"prompt_tokens": 96, "completion_tokens": 3, "total_tokens": 99},
}


def _keyed(settings: Settings) -> Settings:
    """有密钥、零重试:生成器只消费一次上游响应,错误路径更快收敛。"""
    return settings.model_copy(
        update={"siliconflow_api_key": "k-test", "llm_max_retries": 0}
    )


def _context(text: str) -> AnswerContext:
    return AnswerContext(
        text=text,
        sources=[
            ContextSource(
                citation=Citation(index=1, chunk_id="c1", document_id="d1", score=0.9),
                content=text,
            )
        ],
    )


def _generator(
    settings: Settings,
    responses: list[TransportResponse | Exception],
) -> tuple[SiliconFlowAnswerGenerator, FakeTransport]:
    """统一客户端 + 真实 LLM 生成器。"""
    transport = FakeTransport(responses)
    client = UnifiedLLMClient(_keyed(settings), transport=transport)
    return SiliconFlowAnswerGenerator(client), transport


async def test_generate_sends_grounded_prompt(settings) -> None:
    generator, transport = _generator(_keyed(settings), [TransportResponse(200, ANSWER_BODY)])

    result = await generator.generate(
        "入职多久可以申请年假?", context=_context("[1] 年假制度\n入职满一年后可申请年假。")
    )

    assert result.text == "根据 [1],入职满一年后可以申请年假。"
    assert result.usage.total_tokens == 135

    call = transport.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["json"]["model"] == "Qwen/Qwen3-8B"
    assert call["json"]["temperature"] == 0
    messages = call["json"]["messages"]
    assert messages[0]["role"] == "system"
    system_prompt = messages[0]["content"]
    assert "编号资料" in system_prompt
    assert "编造" in system_prompt
    assert REFUSAL_ANSWER in system_prompt
    user_prompt = messages[-1]["content"]
    assert user_prompt.startswith("编号资料:")
    assert "[1] 年假制度" in user_prompt
    assert "入职满一年后可申请年假。" in user_prompt
    assert "入职多久可以申请年假?" in user_prompt


@pytest.mark.parametrize(
    "context", [AnswerContext(text="", sources=[]), AnswerContext(text="   ", sources=[])]
)
async def test_empty_context_refuses_without_touching_upstream(settings, context) -> None:
    generator, transport = _generator(_keyed(settings), [])

    result = await generator.generate("入职多久可以申请年假?", context=context)

    assert result.text == REFUSAL_ANSWER
    assert result.usage.total_tokens == 0
    assert transport.calls == []


async def test_model_refusal_is_returned_verbatim(settings) -> None:
    generator, transport = _generator(_keyed(settings), [TransportResponse(200, REFUSAL_BODY)])
    context = _context("[1] 年假制度\n入职满一年后可申请年假。")

    result = await generator.generate("公司班车几点发车?", context=context)

    assert result.text == REFUSAL_ANSWER
    assert result.usage.total_tokens == 99
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "failure,expected",
    [
        (httpx.ReadTimeout("slow llm"), UpstreamTimeoutError),
        (TransportResponse(429, {}), UpstreamRateLimitedError),
    ],
)
async def test_upstream_errors_propagate_as_unified_codes(
    settings, failure, expected
) -> None:
    generator, _ = _generator(_keyed(settings), [failure])
    context = _context("[1] 年假制度\n入职满一年后可申请年假。")

    with pytest.raises(expected):
        await generator.generate("入职多久可以申请年假?", context=context)
