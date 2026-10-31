# ADR-0016: 机制层换成熟开源库——重试退避、熔断、行映射、余弦、答档淘汰、分批向量化

**Status**: accepted

## Context

ADR-0001 的原则是"能用开源成熟方案就不造轮子",同时"自建领域层作为唯一权威契约"。此前一轮盘点发现:项目的**领域层边界划得相当好**(状态机、评估指标口径、分词口径、响应信封、rerank 薄适配器都经 ADR 论证后自建且应保留),但有若干处**机制层**自己造了轮子,且个别地方自己造轮子时还漏掉了成熟方案自带的能力:

1. `app/integrations/llm/client.py` 手写重试循环、指数退避、`Retry-After` 头解析(秒数 + HTTP 日期两种形态)与跨请求发送节拍 —— 约 100 行,恰好是 tenacity 的职责范围;且**退避没有抖动**,多客户端在免费档配额场景下会同刻重试(惊群)。
2. 上游硬挂(连续 5xx / 连接失败)时,每次请求都要付满 `3 × 30s` 的重试阶梯才失败,没有熔断:硬挂的上游被反复打满,每次都慢到超时。
3. `app/integrations/llm/client.py` 手写 OpenAI 兼容响应的 `isinstance` / `Mapping` 链与 `_as_int` 解析,字段脏数据会让整个响应判为畸形(用量字段本不该打挂问答主链路)。
4. `app/repositories/postgres/*.py` 五个仓储各写一遍 `_row_values` + `_record_from_row` 手写映射,约 250 行样板;列名与领域字段名不同名处(`chunk_index` → `index`、`file_type` → `DocumentFormat`)逐字段手拼。
5. 余弦相似度写了两遍(`app/domain/retrieval.py` 与 `app/services/retrieval.py` 的 `_cosine_between`),且都是纯 Python 循环 —— MMR 的多样性计算是 O(n²·d),候选池上去后解释器循环成为瓶颈。
6. `app/services/qa_cache.py` 手写 LRU 淘汰 + TTL 过期。
7. `app/services/embedding.py` 把整篇文档的全部切片一次打给向量模型 —— 两万段的 docx 会打成单个上游请求,大概率超请求体 / 超时。
8. `scripts/evaluate.py` 的常量有重复赋值与"先使用后定义"(靠 import 顺序侥幸工作);`Settings.model_copy(update=...)` 绕过 pydantic 校验器,带着非法配置跑完整轮评估。

盘点同时确认了**不该换**的部分:状态机表、内存向量库基线、CJK bigram 分词、Recall/MRR/NDCG 指标、规则法忠实度、rerank 薄适配器、响应信封、文本净化、文件存储 —— 换成库要么更差(需要更多配置代码)、要么破坏已发布的评估基线、要么与 ADR-0002 的解析器论证冲突。本 ADR 只动机制层。

## Decision

- **重试 / 退避 / `Retry-After` 等待改由 tenacity 承接**(`AsyncRetrying` + `stop_after_attempt` + `retry_if_exception`):退避改为"指数底数 × 上界内抖动",抖动用**可注入的随机源**(`random_source`),使离线评估与回归断言可复现、生产避免惊群;`Retry-After` 仍优先于本地退避(ADR-0013 的语义不变);`upstream_call_retry` 事件字段不变。
- **上游熔断**:连续可重试失败达 `llm_circuit_breaker_failures`(默认 5)即打开,冷却期(`llm_circuit_breaker_reset_seconds`,默认 30s)内直接快速失败、不再打上游;冷却期满放一次探测请求,成功即恢复。状态(`closed` / `open` / `half-open`)经 `/health` 的 `upstream.circuit_breaker` 可见。客户端 4xx 不计入连续失败。熔断用约 60 行自持实现:只数连续失败与冷却时间,引入状态机库不划算。
- **上游响应解析改由 pydantic 承担**:chat / embeddings / rerank 三类响应各有一个响应模型(别名 + 枚举还原 + jsonb 反序列化);用量字段按宽松口径解析(脏数据按 0 计),不再让用量打挂主链路;chat 的 `content` 兼容"字符串"与"片段数组"两种形态。
- **pg 行映射收口到 `app/repositories/postgres/rows.py`**:列名只在该模块出现一次,pydantic 负责别名 / 枚举 / jsonb 还原;手写 SQL 与 DDL 保持不变(**不引入 ORM**):`seq bigserial` 时序兜底、部分索引(`WHERE is_latest`)、无维度修饰的 `vector` 列、pgvector 原生 `<=>` 运算符与混维降级路径,这些在 ORM 下都要定制或变难,而收益不足以抵过连接池 / 会话生命周期的重写。
- **余弦相似度收口到领域层一份**(`app/domain/retrieval.cosine_similarity`),实现走 numpy 向量化;`services/retrieval.py` 的 `_cosine_between` 改为复用它(缺失向量仍按 0 分,不作惩罚也不作奖励)。三个向量库后端(内存 / Chroma / pgvector)的分数同源口径不变,评估基线逐字不变。
- **答档缓存的 LRU + TTL 淘汰改由 cachetools 的 `TTLCache` 承接**(`timer` 注入让时钟仍可离线控制);键的构成口径、整体失效时机与命中统计仍是领域契约。
- **向量化按批调用**:新增 `embedding_batch_size`(默认 20),整篇文档按批送向量模型;批次向量顺序与切片顺序严格对齐(每批向量数与输入数不符即报 `UPSTREAM_BAD_RESPONSE`,绝不静默错位);批次用量与延迟累加,失败侧仍不留半批向量(向量库整组替换,文档停在 `chunked` 可重试,ADR-0003 语义不变)。
- **配置覆盖重跑校验器**:`Settings.with_overrides(**updates)` 用 `model_validate` 重跑 `model_validator`,脚本层(评估器 / 基准)改用它,跨字段不变量(如 "pgvector 模式必须给 `database_url`"、"双通道权重不可同时为 0")在覆盖后依然生效。
- **`scripts/evaluate.py` 的常量归位**:重复的 `_TOGGLE_FIELDS` 赋值删除,`_GATE_FLAGS` / `_TOGGLE_SPECS` 等常量提到使用点之前。
- **`ChunkFilter` 与 embeddings 列名的漂移改为装配期暴露**:过滤字段集由领域模型推导,出现无对应列的字段时在导入期即报错,而不是运行时 `KeyError`。

## Consequences

- **评估基线不变**:`scripts/evaluate.py` 在确定性替身下的 Recall@3 / MRR / NDCG@3 逐字不变(0.7917 / 0.7500 / 0.7609),numpy 余弦与行映射改造不改变任何口径。
- **新增依赖**:`tenacity`、`numpy`、`cachetools` 三个纯 Python / 轻量依赖(`numpy` 已是 chromadb 的传递依赖,`tenacity` 已是 httpx 的传递依赖);**不引入** ORM、状态机库、向量 ANN 库或 LLM SDK。
- **行为变化(有意)**:退避从确定性变为带抖动的区间;同一随机源下可复现,故回归断言改为区间断言而非精确值。既有测试 `tests/test_llm_client.py` 的三处精确延迟断言按新口径更新。
- **`/health` 响应形状变化**:新增 `upstream.circuit_breaker` 字段(`closed` / `open` / `half-open`);响应信封与其余字段不变。
- **熔断默认开启**(阈值 5 / 冷却 30s),可用 `LLM_CIRCUIT_BREAKER_FAILURES=0` 关闭;阈值与冷却时长可配置。
- **`EmbeddingService` 新增 `batch_size` 构造参数**(默认 20),应用装配点从 `settings.embedding_batch_size` 传入;既有单测与 API 契约不变(批次切分对调用方不可见)。
- **熔断是单进程态**:多实例各自计数,不承诺全局一致的熔断裁决 —— 与 ADR-0013 的"限速只约束本进程"同款边界。
- `app/repositories/postgres/rows.py` 是新增的映射层文件:领域模型不感知数据库列名(ADR-0001 边界不变),列名改动只动这一处。
