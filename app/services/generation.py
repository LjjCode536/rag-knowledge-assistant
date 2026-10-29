"""有据回答生成器的窄接缝:规则版基线,以及可替换为真实 LLM 的协议。

生成器只接收"问题 + 编号资料",不得自行检索或补事实;资料为空时与
服务层一样返回统一拒答文案。T08 用真实 LLM 实现同一协议即可替换。
"""

from __future__ import annotations

from typing import Protocol

from app.domain.qa import REFUSAL_ANSWER, AnswerContext


class AnswerGenerator(Protocol):
    """生成器契约:输入问题与编号资料,返回有据回答或统一拒答文案。"""

    async def generate(self, question: str, *, context: AnswerContext) -> str: ...


class RuleBasedAnswerGenerator:
    """教材第 45 章的规则版基线:只从上下文取事实,不触网、不编造。

    命中已知制度条款时返回固定表述;未命中时退化为"最相关切片原文"的
    抽取式回答。两条路径都要求上下文非空,资料不足一律拒答。
    """

    async def generate(self, question: str, *, context: AnswerContext) -> str:
        if not context.sources or not context.text.strip():
            return REFUSAL_ANSWER

        text = "\n".join(source.content for source in context.sources)
        if "未发货" in text and "退款" in text:
            return "根据知识库资料,订单未发货时,用户可以申请取消订单并退款。"
        if "已发货" in text and "退货" in text and "收到" in text:
            return "根据知识库资料,订单已发货后,用户需要收到商品后再发起退货退款申请。"
        if "物流超过 7 天" in text and "客服" in text:
            return "根据知识库资料,如果物流超过 7 天没有更新,建议联系客服协助核查。"
        if ("企业发票" in text or "纳税人识别号" in text) and "公司名称" in text:
            return "根据知识库资料,企业发票需要填写公司名称和纳税人识别号。"
        if "年假" in text and "入职" in text and "一年" in text:
            return "根据知识库资料,员工入职满一年后,可以按照公司制度申请年假。"
        if (
            "差旅报销" in text
            and "审批记录" in text
            and "发票" in text
            and "行程单" in text
        ):
            return "根据知识库资料,差旅报销需要提交发票、行程单和审批记录。"

        best = context.sources[0].content.strip()
        return f"根据知识库资料,可以参考以下内容:{best}"
