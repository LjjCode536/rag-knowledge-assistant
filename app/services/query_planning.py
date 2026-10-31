"""查询规划:用统一客户端一次 chat 调用产出查询改写 + 多查询变体。

LLM 与 JSON 解析都只是召回质量的增强;任何失败(解析 / 上游)都回退
原查询并留告警日志,检索链路绝不因规划能力挂掉。

JSON 解析由 pydantic 承担(模型校验 + 别名),剥 ``` 围栏后直接校验;
降级语义(失败即回退原查询)是本项目的领域契约,不由框架决定。
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field, ValidationError

from app.core.errors import UpstreamError
from app.domain.retrieval import QueryPlan
from app.integrations.llm.client import UnifiedLLMClient

logger = structlog.get_logger("retrieval")

_PROMPT_TEMPLATE = """你负责企业知识库的检索查询规划。对下面的问题输出一个 JSON 对象,
不要输出 JSON 以外的任何文字。JSON 格式与要求:
{{"rewritten": "改写后的规范化查询(补全口语化和错误用词)", "variants": [{variants_deck}]}}

问题:{question}"""


class _PlanPayload(BaseModel):
    """规划产物:改写(可选)+ 变体数组。

    ``variants`` 里的 null 项按"无此变体"跳过;``rewritten`` 缺字段或为
    null 都视为"没改写"。类型不符则整个响应判为畸形。
    """

    rewritten: str | None = None
    variants: list[str | None] = Field(default_factory=list)


def _strip_code_fence(content: str) -> str:
    """剥掉模型可能加的 ``` 围栏(含 ```json 语言标记)。"""
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
    """解析规划 JSON;畸形响应返回 None(调用方回退原查询)。"""
    try:
        payload = _PlanPayload.model_validate_json(_strip_code_fence(content))
    except ValidationError:
        return None
    rewritten = payload.rewritten.strip() if payload.rewritten else None
    variants = [variant for variant in payload.variants if variant is not None]
    return (rewritten or None), variants


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
