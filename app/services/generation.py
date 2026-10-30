"""有据回答生成器的窄接缝:规则版基线 + 真实 LLM(教材第 45 章,T08)。

生成器只接收"问题 + 编号资料",不得自行检索或补事实;资料为空或模型
判定不足时返回统一拒答文案。``GeneratedAnswer`` 把 Token 用量随结果带回,
``kind`` 记入查询日志,让"用了哪个生成器 / 花了多少 Token"可观测;
上游错误不做静默降级,按 UpstreamError 家族上抛统一错误码(ADR-0007)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.domain.qa import REFUSAL_ANSWER, AnswerContext, GenerationUsage, GeneratorKind
from app.integrations.llm.client import UnifiedLLMClient


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    """一次生成的产物:回答文本 + 生成步消耗的 Token 用量(规则版为零)。

    拒答通过 ``text == REFUSAL_ANSWER`` 精确判定;上游调用失败由
    实现方上抛,不在本数据结构里表达。
    """

    text: str
    usage: GenerationUsage = field(default_factory=GenerationUsage)


class AnswerGenerator(Protocol):
    """生成器契约:输入问题与编号资料,返回有据回答或统一拒答文案。"""

    kind: GeneratorKind

    async def generate(
        self, question: str, *, context: AnswerContext
    ) -> GeneratedAnswer: ...


class RuleBasedAnswerGenerator:
    """教材第 45 章的规则版基线:只从上下文取事实,不触网、不编造。

    命中已知制度条款时返回固定表述;未命中时退化为"最相关切片原文"的
    抽取式回答。两条路径都要求上下文非空,资料不足一律拒答。
    """

    kind: GeneratorKind = GeneratorKind.RULE_BASED

    async def generate(
        self, question: str, *, context: AnswerContext
    ) -> GeneratedAnswer:
        if not context.sources or not context.text.strip():
            return GeneratedAnswer(text=REFUSAL_ANSWER)

        text = "\n".join(source.content for source in context.sources)
        if "未发货" in text and "退款" in text:
            return GeneratedAnswer(
                text="根据知识库资料,订单未发货时,用户可以申请取消订单并退款。"
            )
        if "已发货" in text and "退货" in text and "收到" in text:
            return GeneratedAnswer(
                text="根据知识库资料,订单已发货后,用户需要收到商品后再发起退货退款申请。"
            )
        if "物流超过 7 天" in text and "客服" in text:
            return GeneratedAnswer(
                text="根据知识库资料,如果物流超过 7 天没有更新,建议联系客服协助核查。"
            )
        if ("企业发票" in text or "纳税人识别号" in text) and "公司名称" in text:
            return GeneratedAnswer(
                text="根据知识库资料,企业发票需要填写公司名称和纳税人识别号。"
            )
        if "年假" in text and "入职" in text and "一年" in text:
            return GeneratedAnswer(
                text="根据知识库资料,员工入职满一年后,可以按照公司制度申请年假。"
            )
        if (
            "差旅报销" in text
            and "审批记录" in text
            and "发票" in text
            and "行程单" in text
        ):
            return GeneratedAnswer(
                text="根据知识库资料,差旅报销需要提交发票、行程单和审批记录。"
            )

        best = context.sources[0].content.strip()
        return GeneratedAnswer(text=f"根据知识库资料,可以参考以下内容:{best}")


_SYSTEM_PROMPT = (
    "你是企业知识库的问答助手。你只能依据用户提供的编号资料回答问题,"
    "禁止使用任何资料之外的知识,禁止猜测或编造。\n"
    f"如果资料不足以回答问题,你必须原样只输出:{REFUSAL_ANSWER}\n"
    "回答要用中文,简洁明了,并用 [编号] 标注所依据的资料。"
)


class SiliconFlowAnswerGenerator:
    """真实 LLM 的有据回答:统一客户端 chat 薄适配 + 强约束 Prompt。

    Prompt 契约:只依据编号资料回答、不足时原样输出统一拒答文案、
    绝不编造。上游错误(超时 / 限流等)原样上抛,由调用方统一转成
    错误码——生成是产品本体,不做静默降级。
    """

    kind: GeneratorKind = GeneratorKind.LLM

    def __init__(self, llm_client: UnifiedLLMClient) -> None:
        self._client = llm_client

    async def generate(
        self, question: str, *, context: AnswerContext
    ) -> GeneratedAnswer:
        if not context.sources or not context.text.strip():
            return GeneratedAnswer(text=REFUSAL_ANSWER)

        result = await self._client.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"编号资料:\n{context.text}\n\n问题:{question}"},
            ],
            temperature=0,
        )
        return GeneratedAnswer(
            text=result.content.strip(),
            usage=GenerationUsage(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                total_tokens=result.usage.total_tokens,
            ),
        )
