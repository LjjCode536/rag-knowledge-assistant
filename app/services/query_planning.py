"""查询规划:用统一客户端一次 chat 调用产出查询改写 + 多查询变体。

LLM 与 JSON 解析都只是召回质量的增强;任何失败(解析 / 上游)都回退
原查询并留告警日志,检索链路绝不因规划能力挂掉。
"""

from __future__ import annotations

import json

import structlog

from app.core.errors import UpstreamError
from app.domain.retrieval import QueryPlan
from app.integrations.llm.client import UnifiedLLMClient

logger = structlog.get_logger("retrieval")

_PROMPT_TEMPLATE = """你负责企业知识库的检索查询规划。对下面的问题输出一个 JSON 对象,
不要输出 JSON 以外的任何文字。JSON 格式与要求:
{{"rewritten": "改写后的规范化查询(补全口语化和错误用词)", "variants": [{variants_deck}]}}

问题:{question}"""


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) >= 2 else ""
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _dedupe_variants(variants: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in variants:
        stripped = (item or "").strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            cleaned.append(stripped)
    return cleaned


def _parse_plan(content: str) -> tuple[str | None, list[str]] | None:
    stripped = _strip_code_fence(content)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    rewritten = parsed.get("rewritten")
    variants = parsed.get("variants")
    if rewritten is not None and not isinstance(rewritten, str):
        return None
    if not isinstance(variants, list) or any(
        variant is not None and not isinstance(variant, str) for variant in variants
    ):
        return None
    return (rewritten or None), [variant for variant in variants if variant is not None]


class SiliconFlowQueryPlanner:
    """统一客户端 chat 的薄适配:产出改写与变体,失败回退原查询。"""

    def __init__(self, llm_client: UnifiedLLMClient) -> None:
        self._client = llm_client

    async def plan(self, question: str, *, variant_count: int) -> QueryPlan:
        """一次调用同时产出重写与变体;任何失败都退回原查询口径。"""
        variants_deck = ", ".join(
            f'"变体{index}"' for index in range(1, max(variant_count, 0) + 1)
        )
        prompt = _PROMPT_TEMPLATE.format(question=question, variants_deck=variants_deck)
        try:
            result = await self._client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0,
            )
        except UpstreamError as exc:
            logger.warning(
                "query_plan_degraded",
                fallback="original_query",
                code=exc.code.value,
                status_code=exc.status_code,
            )
            return QueryPlan(original=question)

        parsed = _parse_plan(result.content)
        if parsed is None:
            self._log_parse_degraded(question)
            return QueryPlan(original=question)
        rewritten, variants = parsed
        return QueryPlan(
            original=question,
            rewritten=rewritten if rewritten != question else None,
            variants=_dedupe_variants(variants),
        )

    def _log_parse_degraded(self, question: str) -> None:
        logger.warning(
            "query_plan_degraded",
            fallback="original_query",
            question_chars=len(question),
        )
