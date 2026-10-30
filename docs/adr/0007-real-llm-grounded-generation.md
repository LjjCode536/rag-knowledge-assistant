# ADR-0007: 真实 LLM 有据生成:Prompt 强约束、生成结果契约与查询日志留痕

**Status**: accepted

## Context

T08(教材第 45 章任务)要把 M3 的规则版回答基线替换为真实 LLM(Qwen/Qwen3-8B,可配置)。替换发生在与 ADR-0004 同一条生成器接缝上,但有三个新契约要先定下来:

1. **不编造如何落实**:规则版靠"只从上下文取事实"实现;真实 LLM 必须 Prompt 强约束 + 明确拒答口径,并且知不知道拒答要走同一把尺子(统一拒答文案精确判定)。
2. **生成步的留痕**:检索命中与延迟已在 QueryLog 里,但 LLM 生成的 Token 用量、用的是哪种生成器,以及检索侧改写 / 扩展后真正生效的查询口径,此前无处安放。
3. **上游错误语义**:查询规划把上游失败降级为原查询(增强能力,可降级);生成是产品本体,回答直接依赖它,降级等于把编造放进来。

## Decision

- **Prompt 强约束**:system 消息声明"只依据编号资料回答、不足即原样输出统一拒答文案、绝不编造",user 消息携带编号资料与问题;`temperature=0` 保证拒答行为稳定。资料为空时本地直接拒答,不触上游。
- **生成结果契约**:`AnswerGenerator.generate` 返回 `GeneratedAnswer(text, usage)`——文本照旧按 `== REFUSAL_ANSWER` 精确判定拒答(ADR-0004 预告的扩展),Token 用量随结果带回;协议新增 `kind` 属性(`rule_based` / `llm`)。规则版与注入替身一并迁移到同一契约。
- **生成器选择**:`QA_GENERATOR`(`rule` / `llm`,默认 `llm`)在装配处选择实现,注入替身时不看配置;两种模式经 `generator` 字段在响应与查询日志中可观测。
- **上游错误不降级**:生成步的上游错误(超时 / 限流等)原样上抛,由异常处理器统一转成 `UPSTREAM_TIMEOUT` / `UPSTREAM_RATE_LIMITED` 等稳定错误码;与查询规划的"失败回退"形成对照——增强可降级,本体不可。
- **查询日志留痕**:`QueryLog` 新增 `generator`、`token_usage`、`rewritten_question`、`expanded_questions`;后两者只记实际参与召回的口径(检索服务经 `RetrievalOutcome` 返回生效查询,开关未开 / 规划回退时为 None / 空)。仓储契约与 API 路由不变,仅在视图透出新字段。

## Consequences

- **收益**:模型内生成有据回答带引用回显;拒答与 Token 用量、生效查询口径全部可追溯,为 T09 的生成质量指标(Faithfulness / Citation Accuracy)与 T13 的日志持久化提供同一口径;规则方式仍可作为 `QA_GENERATOR=rule` 的离线基线复现 T05 行为。
- **代价**:拒答可靠性依赖 Prompt 契约而非逻辑保证(真实验证在 `scripts/check_upstream.py` 中带上游跑一次);`QueryLog` 与两个 API 视图新增字段,旧调用方按"只增不减"兼容;测试替身需随契约补 `kind` 与用量。
- **后续**:生成质量评估(Faithfulness / Citation Accuracy)在 T09 引入时复用本日志口径;模型不做 Prompt 之外的输出校验,若上游模型行为漂移导致编造率上升,由生成指标兜底发现。
