"""查询规划器缝:查询改写 + 多查询扩展(LLM 驱动、FakeTransport、不触网)。

验收口径:
- 一次 chat 调用同时产出改写与变体(结构化 JSON,教材第 41 章生产形态);
- JSON 解析失败 / 上游任何失败 → 回退原查询,口径只增不减;
- 变体数量可裁剪、可去重,空串剔除。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import configure_logging
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse
from app.services.query_planning import SiliconFlowQueryPlanner
from tests.support import json_log_entries
from tests.test_llm_client import FakeTransport

PLAN_OK_BODY = {
    "id": "chatcmpl-1",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": '```json\n{"rewritten": "未发货订单退款流程", '
                '"variants": ["下单后未发货能退款吗", "发货前退款申请"]}\n```',
            },
            "finish_reason": "stop",
        }
    ],
}


def _keyed_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"siliconflow_api_key": "k-test", "llm_max_retries": 0}
    )


def _planner(
    settings: Settings,
    responses: list[TransportResponse | Exception],
) -> tuple[SiliconFlowQueryPlanner, FakeTransport]:
    transport = FakeTransport(responses)
    client = UnifiedLLMClient(settings, transport=transport)
    return SiliconFlowQueryPlanner(client), transport


async def test_plan_parses_rewrite_and_variants(settings) -> None:
    planner, _ = _planner(_keyed_settings(settings), [TransportResponse(200, PLAN_OK_BODY)])

    plan = await planner.plan("没发货可以退钱吗?", variant_count=3)

    assert plan.original == "没发货可以退钱吗?"
    assert plan.rewritten == "未发货订单退款流程"
    assert plan.variants == ["下单后未发货能退款吗", "发货前退款申请"]


async def test_plan_sends_question_with_json_contract(settings) -> None:
    planner, transport = _planner(_keyed_settings(settings), [TransportResponse(200, PLAN_OK_BODY)])

    await planner.plan("没发货可以退钱吗?", variant_count=2)

    call = transport.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["json"]["model"] == "Qwen/Qwen3-8B"
    assert call["json"]["temperature"] == 0
    user_prompt = call["json"]["messages"][-1]["content"]
    assert "没发货可以退钱吗?" in user_prompt
    assert "JSON" in user_prompt
    assert "2" in user_prompt


async def test_plan_trims_and_dedupes_variants(settings) -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": '{"rewritten": "原始查询", "variants": '
                    '["未发货退款", " 未发货退款 ", "", "发货前退款", "未发货退款"]}'
                }
            }
        ]
    }
    planner, _ = _planner(_keyed_settings(settings), [TransportResponse(200, body)])

    plan = await planner.plan("没发货可以退钱吗?", variant_count=3)

    assert plan.variants == ["未发货退款", "发货前退款"]


async def test_malformed_json_falls_back_to_original_query(
    settings, capsys
) -> None:
    keyed = _keyed_settings(settings)
    configure_logging(keyed)
    body = {"choices": [{"message": {"content": "抱歉,我无法输出 JSON"}}]}
    planner, _ = _planner(keyed, [TransportResponse(200, body)])

    plan = await planner.plan("没发货可以退钱吗?", variant_count=3)

    assert plan.rewritten is None
    assert plan.variants == []
    entries = json_log_entries(capsys.readouterr().out)
    degraded = [entry for entry in entries if entry.get("event") == "query_plan_degraded"]
    assert len(degraded) == 1
    assert degraded[0]["fallback"] == "original_query"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("slow llm"),
        TransportResponse(429, {}),
        TransportResponse(500, {}),
    ],
)
async def test_upstream_failure_falls_back_to_original_query(
    settings, capsys, failure
) -> None:
    configure_logging(settings)
    planner, _ = _planner(_keyed_settings(settings), [failure])

    plan = await planner.plan("没发货可以退钱吗?", variant_count=3)

    assert plan.rewritten is None
    assert plan.variants == []
    entries = json_log_entries(capsys.readouterr().out)
    degraded = [entry for entry in entries if entry.get("event") == "query_plan_degraded"]
    assert len(degraded) == 1
