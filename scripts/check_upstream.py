"""真实上游(硅基流动)连通性检查。

不属于常规测试路径:需要 SILICONFLOW_API_KEY,会产生真实计费调用。
用法::

    uv run python scripts/check_upstream.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.domain.qa import (
    REFUSAL_ANSWER,
    AnswerContext,
    Citation,
    ContextSource,
)
from app.integrations.llm.client import UnifiedLLMClient
from app.services.generation import SiliconFlowAnswerGenerator

HANDBOOK_CONTEXT = AnswerContext(
    text=(
        "[1] 文档:after_sales.md | 标题:未发货退款\n"
        "订单尚未发货时,用户可以申请取消订单并全额退款。"
    ),
    sources=[
        ContextSource(
            citation=Citation(index=1, chunk_id="c1", document_id="d1", score=0.9),
            content="订单尚未发货时,用户可以申请取消订单并全额退款。",
        )
    ],
)


async def main() -> int:
    settings = get_settings()
    if not settings.upstream_configured:
        print("SILICONFLOW_API_KEY 未配置,跳过连通性检查。", file=sys.stderr)
        return 1

    client = UnifiedLLMClient(settings)
    exit_code = 0
    try:
        chat = await client.chat(
            [{"role": "user", "content": "用一句话回答:1+1 等于几?"}],
            max_tokens=64,
        )
        print(f"chat  -> {chat.content!r} usage={chat.usage} latency={chat.latency_ms}ms")

        embed = await client.embed(["连通性检查"])
        print(f"embed -> dim={len(embed.vectors[0])} usage={embed.usage}")

        rerank = await client.rerank("年假怎么算?", ["年假按工龄计算", "报销需要发票"])
        print(f"rerank -> {[(item.index, round(item.score, 4)) for item in rerank.items]}")

        # T08 验收:真实 LLM 生成的有据回答与"上下文不足稳定拒答"各验证一次。
        generator = SiliconFlowAnswerGenerator(client)
        grounded = await generator.generate("没发货可以退钱吗?", context=HANDBOOK_CONTEXT)
        print(f"answer -> {grounded.text!r} usage={grounded.usage}")

        refusal = await generator.generate("公司班车几点发车?", context=HANDBOOK_CONTEXT)
        print(f"refusal -> {refusal.text!r} usage={refusal.usage}")
        if refusal.text != REFUSAL_ANSWER:
            print(
                f"拒答文案偏离统一口径(期望 {REFUSAL_ANSWER!r}),检查 Prompt 约束。",
                file=sys.stderr,
            )
            exit_code = 1
    finally:
        await client.aclose()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
