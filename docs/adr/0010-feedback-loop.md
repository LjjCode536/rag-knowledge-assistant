# ADR-0010: 反馈闭环挂靠查询日志;一问一评;差评按问题聚成黄金用例候选

**Status**: accepted

## Context

T11(反馈闭环,教材第 45 章评估闭环的线上入口)之前,线上问答只有单向输出:用户拿到回答后,系统收不到"这个回答到底有没有用"的信号,差评来源-question 要人工去日志里翻。查询日志(T05)已经完整记录了每次问答的问题、答案、拒答原因与命中,给反馈提供了天然挂靠点。

四个实现取舍需要记录:

1. **身份锚点**:反馈挂在什么上——query_id,前台可拿到的唯一稳定句柄。
2. **trace_id 归属**:反馈请求与原问答是两个 HTTP 请求,trace_id 不同;反馈记录上应记哪一个。
3. **重复提交**:同一问答再次提交反馈是追加一条,还是覆盖。
4. **差评如何变成"可评估"的东西**:黄金数据集要求稳定复现,而原始差评只有一句话。

## Decision

- **反馈 = FeedbackRecord,身份锚点是 query_id**。提交反馈必须给出一条已存在的查询日志的 `query_id`;查不到即 404(NOT_FOUND,details 带 `query_id`)。这保证每条反馈都有完整的"问题-答案-命中"上下文可追溯,反馈不可能凭空存在。
- **trace_id 从查询日志同源取,不信任调用方自报**。反馈记录复制原日志的 `trace_id`,反馈请求自己的 trace_id 只出现在响应信封里(GLOSSARY trace_id 语义)。这样"按 trace_id 查链路"时,原问答与对它的反馈天然对齐,伪造关联也不可能发生。
- **一问一评,重提交即 upsert 覆盖**。同一 query 的现行反馈唯一,再次提交(含改判 useful ↔ useless)覆盖 verdict 与 comment,`feedback_id` 与首次提交的 `created_at` 不变——反馈代表用户当下对该回答的判定,不是流水账;仓储层只提供同 id 覆盖的 `add`,upsert 语义由服务层实现。生产换 PostgreSQL 时同一语义由该表 `(query_id)` 唯一约束继续成立。
- **差评按问题原文聚组为 GoldenCaseCandidate,频次高 / 时间近的排前面**。候选 `id` 由问题文本 md5 确定性生成,同一问题跨导出保持稳定,便于人工跟踪与去重;`failure` 归因出两态:`refusal:<拒答原因>`(误拒答了能回答的问题)与 `answer_not_useful`(答了但没用),混合差评记 `mixed`。候选携带 origins(每次差评的 query_id、trace_id、当时的命中文档、comments),人工复核后整理进 `evaluation/golden_dataset.yaml`。
- **导出走同一条 GET**:`GET /api/v1/feedback/negative/candidates` 默认回 JSON 信封,`?format=csv` 返回 text/csv 文件(带 UTF-8 BOM,Excel 直开中文不乱码)——"可结构化导出"不需要第二种聚合口径,JSON 与 CSV 出同一份聚合数据;CSV 行内只留 query_id / comments 两列锚点,完整 origin 上下文回 API 复核。
- **仓储契约新增 `FeedbackRepository`**,M4 进程内存实现,生产形态换 PostgreSQL(feedbacks 表),契约不变(ADR-0001)。

## Consequences

- 反馈与查询日志强耦合:查询日志是内存实现,重启即失忆,反馈随之失去关联对象(提交时变 404)。M3 上 PostgreSQL 后两者同库,关联持久化。
- 聚组键是问题原文精确匹配:同一意图换个说法仍是两个候选。这是刻意的保守选择——自动规范化(去标点 / 同义词合并)引入错聚风险,宁可多组也不要错并;组内 comments 已暴露可人工合并的线索。
- 候选只是"候选":反馈不自动改写黄金数据集,入库前必须人工补预期切片锚点——锚点判断依赖领域知识,自动化会污染基准(GLOSSARY"预期切片锚点")。
- useful 反馈当前只记录不产出指标:回答侧的"有用率"留待生成指标后续迭代使用,通道已打通。
