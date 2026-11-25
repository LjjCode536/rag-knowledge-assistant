# ADR-0019: 向量来源身份三列与维度守卫;检索只与同一身份互算

**Status**: accepted

## Context

规范要求「入库与查询必须使用同一 Embedding 模型、同一版本、同一维度」,并把模型标识随向量持久化。本仓库此前只存了向量本身:

- `embeddings` 没有 `embedding_model` / `embedding_version` / `embedding_dimension`,线上向量不携带来源。**同维度换模型**(如 1024 维的 bge-m3 换成另一个 1024 维模型)时,旧向量与新查询向量会被直接混算余弦,系统无感、结果静默变差;只有换到不同维度才会被察觉。
- `embedding` 列不带维度修饰(ADR-0011 为迁移期混维共存而设计),维度不符按 0 分降级:把配置 / 模型错配伪装成"知识库没有相关内容"。
- `VectorStore.replace(document_id, chunks, vectors)` 与 `search(vector, ...)` 都没有地方表达"这批向量由谁产生",模型身份只能靠外部约定。

## Decision

1. **身份是写入的最小单元**。领域层新增 `EmbeddingRef(model, version, dimension)` 与 `EmbeddedChunk(chunk, vector, embedding)`;`VectorStore.replace(document_id, chunks: Sequence[EmbeddedChunk])`。`build_embedded_chunks` 统一校验切片数、身份一致与维度自洽;向量库实现再调 `consistent_embedding_ref` 复核。同一批混入两种身份是程序错误(`ValueError`),不静默按先到者落库。
2. **身份来自配置声明**。`EmbeddingService` 与 `RetrievalService` 都从 `Settings.embedding_model / embedding_version / embedding_dimension` 构造同一个 `EmbeddingRef`;写库与查询同源,不信任上游回显的模型名。
3. **检索带身份且先过滤**。`VectorStore.search(vector, *, top_k, filters, embedding)` 是必要参数:内存按 `entry.embedding == embedding` 过滤,Chroma 写进 metadata 并作为 `where` 条件,pgvector 落三列并下推 SQL。身份未知的历史行(NULL)不参与检索 —— 检索里"消失"、表现为拒答,而不是与当前模型混算。
4. **维度在写入与查询两端断言**。模型输出维度与声明不符即抛 `DimensionMismatchError`(携带 chunk_id / document_id / 期望与实际维度),整篇不落库、文档停在 `chunked`;查询向量维度不符同样拒绝,不降级成 0 分候选。
5. **启动只做加法**。`ensure_schema` 对 `embeddings` 只做 `ADD COLUMN IF NOT EXISTS`(三列可空),不在启动路径收紧列类型、加 `CHECK` 或回填。向量列继续保持无维度修饰;定维列 + HNSW 留给独立脚本与人工确认。
6. **历史行按实际维度回填**。`scripts/backfill_embedding_identity.py` 默认 dry-run,只回填 `vector_dims(embedding)` 与目标维度一致且身份三列全为 NULL 的行;维度不符的行保持 NULL 并单独报告(必须整篇重嵌,不能被错误标注)。
7. **启动盘点只告警**。应用启动时统计库内身份分布,存在非当前身份 / 未知身份的向量即打 `embedding_identity_mismatch` 告警(带计数),但不阻断启动:旧向量已被检索隔离,服务仍可服务当前身份的文档。

## Consequences

- 同维度换模型从"静默降分"变成"检索时可见的隔离 + 启动告警";换模型必须整库重嵌,但不会再污染当前结果。
- `VectorStore` 契约是破坏性变更:三个后端实现、全部测试与调用点同步改为 `replace(..., EmbeddedChunk)` / `search(..., embedding=...)`;检索服务的测试替身需把 `Settings.embedding_dimension` 调到与替身维度一致。
- 迁移期旧文档会暂时从检索结果中消失(拒答),直到回填或重嵌完成 —— 这是有意的"诚实失败"。关键词通道不按来源身份隔离(词法召回不依赖向量),混合检索下旧文档仍可能经词法通道出现;需要彻底隔离时先关混合检索或先完成重嵌。
- ADR-0011 中"混维共存、维度不一致按 0 分降级"的部分由本 ADR 取代;该 ADR 的其他决定(单一开关切后端、自足快照、精确扫描)继续有效。
- 暂不解决(保留为后续迭代):定维列 + HNSW 索引、embedding 向量缓存、同步 psycopg 调用对事件循环的影响。
