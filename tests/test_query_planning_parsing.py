"""查询规划器缝:JSON 解析的畸形输入处理(pydantic 校验口径)。

规划是召回质量的增强,任何解析失败都回退原查询 —— 但"失败"的口径
必须明确:哪些响应算畸形、哪些算可降级的脏数据。
"""

from __future__ import annotations

from app.core.config import Settings
from app.integrations.llm.client import UnifiedLLMClient
from app.integrations.llm.transport import TransportResponse
from app.services.query_planning import SiliconFlowQueryPlanner
from tests.test_llm_client import FakeTransport


def _planner(settings: Settings, content: str):
    keyed = settings.model_copy(
        update={"siliconflow_api_key": "k-test", "llm_max_retries": 0}
    )
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    transport = FakeTransport([TransportResponse(200, body)])
    client = UnifiedLLMClient(keyed, transport=transport)
    return SiliconFlowQueryPlanner(client)


async def test_plan_accepts_plain_json_without_code_fence(settings):
    """无围栏的裸 JSON 也应解析成功(模型不一定加围栏)。"""
    planner = _planner(settings, '{"rewritten": "退款流程", "variants": ["未发货退款"]}')

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten == "退款流程"
    assert plan.variants == ["未发货退款"]


async def test_plan_tolerates_null_variants_entries(settings):
    """variants 里的 null 项跳过,不视为畸形。"""
    planner = _planner(
        settings,
        '{"rewritten": "退款流程", "variants": ["未发货退款", null]}',
    )

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten == "退款流程"
    assert plan.variants == ["未发货退款"]


async def test_plan_tolerates_missing_rewritten_field(settings):
    """缺 rewritten 字段:只取变体,不视为畸形。"""
    planner = _planner(settings, '{"variants": ["未发货退款"]}')

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten is None
    assert plan.variants == ["未发货退款"]


async def test_plan_rejects_non_string_rewritten(settings):
    """rewritten 不是字符串:畸形响应,回退原查询。"""
    planner = _planner(settings, '{"rewritten": 123, "variants": []}')

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten is None
    assert plan.variants == []


async def test_plan_rejects_variants_not_a_list(settings):
    """variants 不是数组:畸形响应,回退原查询。"""
    planner = _planner(settings, '{"rewritten": "退款流程", "variants": "abc"}')

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten is None
    assert plan.variants == []


async def test_plan_rejects_json_array_root(settings):
    """根是数组而非对象:畸形响应,回退原查询。"""
    planner = _planner(settings, '["rewritten", "variants"]')

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten is None
    assert plan.variants == []


async def test_plan_drops_rewritten_equal_to_original(settings):
    """改写与原查询相同时视为"没改写":记 None,避免同值重复。"""
    planner = _planner(settings, '{"rewritten": "没发货可以退钱吗?", "variants": []}')

    plan = await planner.plan("没发货可以退钱吗?", variant_count=2)

    assert plan.rewritten is None
