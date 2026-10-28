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
from app.integrations.llm.client import UnifiedLLMClient


async def main() -> int:
    settings = get_settings()
    if not settings.upstream_configured:
        print("SILICONFLOW_API_KEY 未配置,跳过连通性检查。", file=sys.stderr)
        return 1

    client = UnifiedLLMClient(settings)
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
    finally:
        await client.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
