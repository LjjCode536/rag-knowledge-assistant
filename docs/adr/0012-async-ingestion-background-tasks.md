# ADR-0012: 异步 ingestion——上传即时返回任务句柄,重活转后台;同步形态显式配置

**Status**: accepted

## Context

M1–M5 的离线入库链路以"请求内同步"演进:上传即解析(T02)在请求内完成,切片(T03)与向量化(T04)经手动端点串联。spec(M7,issue #15)要求:大文件的解析 / 切片 / 向量化不阻塞 API,上传立即返回任务句柄,进度由文档状态机驱动,完成(indexed)即可问答。

同步形态在"大文件"场景下的问题:上传响应耗时 ≈ 解析耗时(实测 20000 段 docx 约 60s,见 `docs/iterations/0006-t14-async-ingestion.md`),期间请求线程被占满,且解析中途没有进度可查——进度语义被硬编码在响应里。

## Decision

- **上传端点只做"登记"**:`IngestionService.register` 校验格式、同标识旧版本退位(ADR-0009)、登记档案并落盘,立即返回 201;响应中的文档档案(status=`uploaded`)即**任务句柄**——管线推进由文档状态机驱动,`GET /api/v1/documents/{id}` 即进度查询,不另设任务表。
- **管线执行体复用手动端点的同一服务**:`IngestionPipeline` 串联 `parse → chunk → embed_document`,状态机落点与失败语义逐一同前;解析 / 切片在线程池执行,不阻塞事件循环,向量化沿用 `EmbeddingService` 的异步调用。
- **失败口径不变,新增兜底**:
  - 解析 / 切片失败 → `failed` + `error_message`(原语义),重传同标识即重试(ADR-0009);
  - 上游向量化失败 → 不落 failed,档案停在 `chunked`(ADR-0003),重试走 `POST /{id}/embeddings`;
  - 管线自身非预期异常 → 兜底落 `failed` 并带原因,绝不静默;
  - 管线进行中档案被退位 / 删除(并发生命周期迁移)→ 让位终态,管线中止,不产出陈旧入库结果。
- **`INGESTION_ASYNC` 配置(默认 true)**:false 时同一服务在请求内同步串行,供评估器固定切片参数口径、切分参数排查与既有契约测试(见 Consequences)。
- **BackgroundTasks 为起步实现**:同进程事件循环内运行,进程重启丢任务、无重试队列、无租多节点去重——已知边界,以"档案停在的中间态 + status 机 + 重传幂等"为恢复口径;容量证明需要时再引入队列基础设施,接缝保持 `IngestionPipeline` 不动。

## Consequences

- `POST /api/v1/documents` 响应不再携带解析结果(element_count / page_count 等),由 `GET /{id}` 下发;依赖解析字段同步回包的调用方(评估器 `scripts/evaluate.py`)固定 `ingestion_async=false`。
- `EMBEDDING` 状态保持请求内瞬时态(ADR-0003:上游失败落回 `chunked` 的可重试语义,先迁状态会破坏它),仍不可持续观察;`parsing` / `chunking` 进入即持久化,慢解析 / 慢切片期间的进度查询可见。
- 新增管线结构化日志:`document_ingest_finished`(收口 + status)、`document_ingest_upstream_failed`(向量化上游失败)。`document_parse_failed` / `document_chunking_failed` / `document_embedded` 等沿用服务层事件。
- 测试口径分层:`conftest.settings` 固定 `ingestion_async=False` 保持 T02–T13 契约不变;T14 验收在 `tests/test_ingestion_async_api.py` 显式开启 True(异步形态的全量接缝测试)。
