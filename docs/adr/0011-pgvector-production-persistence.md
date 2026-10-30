# ADR-0011: 生产持久化切 pgvector;六张生产表;无 Docker 回退内存 / Chroma

**Status**: accepted

## Context

T12 之后向量库有 Chroma 持久后端,但文档档案、切片、查询日志与反馈仍住在进程内存里(见 `app/repositories/memory.py` 的 M2 基线注记),重启进程全部失忆;评估运行只以文件形态落 `docs/iterations`。教材第 42 / 45 章的生产表设计显然不是这个状态:六张表(documents / chunks / embeddings / query_logs / feedbacks / evaluation_runs)与 PostgreSQL 16 + pgvector 才是生产目标(Docker Compose 一键拉起)。

选型与结构需要回答四个问题:

1. 仓储与向量库如何共享一个 PostgreSQL 后端而不动领域契约(ADR-0001:契约不变)。
2. 召回 / 打分口径如何在第三个后端复现"同一语义检索、结果一致可验证"(T12 的衔接点)。
3. `evaluations` 留档是否新契约(六张表之一此前没有对应仓储)。
4. 无 Docker 环境怎么办(验收:可回退运行,行为一致)。

## Decision

- **切换粒度 = 由 `VECTOR_STORE=pgvector` 一个开关,仓储 + 向量库同切**。仓库不会被拆成"档案在 PG、向量在 Chroma"的混合形态:`PgDatabase`(`psycopg3` 连接池,autocommit)装配一个实例,四仓储(documents / chunks / query_logs / feedbacks)与 `PgVectorStore` 共用。构造即执行幂等建库(`CREATE EXTENSION IF NOT EXISTS vector` + 六张表全部 `IF NOT EXISTS`),`scripts/init_pg.py` 走同一入口,重复执行安全;库不可达在装配期显式失败(`PgUnavailableError`),**绝不静默回退内存态**——生产持久化悄悄降级是危害而不是容错。
- **评分与过滤口径与内存基线 / Chroma 同源**:
  - 分数不用 Postgres 原生距离,候选统一在 Python 用领域层 `cosine_similarity` 复算,三个后端分数同源,仅剩 vector 列物化为 float32 的 1e-5 级精度差(与 Chroma 一致);
  - 检索前过滤下推 SQL `WHERE`(`tenant / department / doc_type / version / document_id` 等值),被过滤切片不进候选,先筛后算;
  - `embeddings` 为**自带切片元数据快照的自足存储**——切片表记录文档域的规范切片,向量表存检索面的快照(与 Chroma 的 metadata 形态一致)。这么选是因为向量的生死必须只由 `VectorStore.replace / delete` 决定:重切作废旧切片后、重嵌入完成前,内存 / Chroma 仍按旧快照检索,快照式存储保持三者语义一致(以及仓储测试可以从独立接缝验证向量库,不必先建档案 / 切片记录)。
- **`embedding` 列不带维度修饰(精确扫描,不建 ANN 索引)**:换嵌入模型的迁移期需要新旧维度共存,混维要求放弃定维修饰,HNSW 无法建立;当前语料量级下 `ORDER BY embedding <=> :q` 的精确扫描足够,升维定容后换定维列 + HNSW 属后续迭代。混维场景(维度不一致)在 Python 复算阶段按 0 分降级,与内存基线 / Chroma 同语义,检索接口不因此 5xx。
- **evaluation_runs 作为新增留档仓储**:`EvaluationRunRepository.add(report, run_id)` 落 `evaluation_runs`(完整报告 JSONB + 摘要列),`scripts/evaluate.py --record-run` 入口收口;评估报告文件照旧落 `docs/iterations`,库内留档供跨迭代追溯。进程内实现(`InMemoryEvaluationRunRepository`)覆盖 PROTOCOL 与测试;契约只增不改(ADR-0001 同款允许"只加方法")。
- **无 Docker 回退**:默认 `VECTOR_STORE=memory`(零依赖);chroma 模式零变动;pgvector 模式需要库可达且显式配置 `DATABASE_URL`。三个形态之外不提供第五种(SQLite 等)"伪生产"形态——回退目标是开发便利,不是以文件形态伪装生产持久化。

## Consequences

- 全链路(入库 → 问答 → 日志 → 反馈)与"重启容器数据仍在"在真库上验证(`tests/test_repositories_pg.py`),迁移幂等经 `ensure_schema` 连续执行测试与 `scripts/init_pg.py` 双重覆盖。
- 四仓储当前契约不变——新仓储层不改变 `RetrievalService` / 生命周期编排的任何调用;`supersede` / `delete` 的"清理先行、状态翻转后置"语义由既有服务层顺序保持。
- 三个后端的检索接口在单元接缝的同题同序同分断言下保证一致,评估器 `--vector-store pgvector` 支持同一黄金数据集跑分对比。
- 评估语料写入 pgvector 后不做整库清空,语料按文档整组覆盖:评估之间靠新上传的文档 id 区隔,需要干净库时用 `rag_test` 或 `docker compose down -v`。
- 附录:无 Docker 时 `docker compose up -d db` 不可用是环境事实,不算回退失败;CI 与常规测试走内存口径,`tests/pg_utils.py` 在库不可达时整批跳过 PG 测试,保持 `pytest` 不依赖外部服务。
