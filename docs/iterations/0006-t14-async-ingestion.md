# T14 异步入库管线(M7·上):上传阻塞时长对比

> 仓库约定"每次迭代产出可复现的前后指标对比"。T14 的可控指标不是检索质量
> (入库内容、切分与检索路径与 T13 相同),而是**上传响应阻塞时长**:大文件的
> 解析 / 切片 / 向量化是否还压在 HTTP 请求里。基准脚本(确定性向量替身,无密钥):

```bash
uv run python scripts/benchmark_ingestion.py            # 默认 20000 段 docx
```

## 基准配置

| 配置 | 值 |
| --- | --- |
| 工作负载 | `handbook.docx` × 20000 段(60000 个解析元素) |
| 形态 A(before) | `INGESTION_ASYNC=false`:M1 的请求内同步解析 |
| 形态 B(after) | `INGESTION_ASYNC=true`(生产默认):登记 + 后台管线 |
| 服务形态 | 真实 uvicorn(临时端口),httpx 走真 socket |
| 切分 / 向量 | `table_aware / 500 / 50`;`deterministic-lexical-1024d` 替身 |
| 运行时间 | 生成于 2026-10-30 基准(见下表) |

## 指标对比

| 指标 | 同步(M1 口径) | 异步(T14,M7) |
| --- | ---: | ---: |
| 上传 POST 阻塞(任务句柄返回) | 60418 ms | **38 ms** |
| 到入库终点(indexed)端到端 | 60418 ms(POST 前已完成解析) | 73263 ms(后台) |

解读:

- **before**:上传 POST 的 60.4s 大头是解析(单段请求线程整段占满,无进度可查);切片与向量化还要再手动两跳,每跳同样阻塞。
- **after**:POST 38ms(登记与落盘),解析 / 切片 / 向量化由 `IngestionPipeline` 后台完成;73.3s 的端到端一段全部变成可查询的状态机进度(`parsing → chunking → embedding → indexed`),问答面在收口处打开。
- 63 秒一次 POST → 服务器"看起来死了",与 38ms 一次重传,是本迭代要消灭的差异。

## 验收(AP,全部经 HTTP 主缝,`tests/test_ingestion_async_api.py`)

- [x] 大文档上传立即返回,不再阻塞请求 —— `test_upload_returns_immediately_and_pipeline_brings_document_indexable`
- [x] 进度经状态机可查,失败带原因且可重试 —— `test_parse_failure_lands_failed_with_reason_and_reupload_retries`、`test_upstream_embed_failure_keeps_document_chunked_and_manual_retry_indexes`、异步 / 同步同口径对比 `test_async_and_sync_paths_land_same_state_and_same_search_results`
- [x] 异步与同步路径行为一致,API 主缝测试全绿 —— 全量 `uv run pytest` 272 passed(含既有 T02–T13 契约测试,固定同步口径)

## 行为口径(ADR-0012)

- 任务句柄 = 上传响应中的文档档案(`status=uploaded`),无任务表;进度查询 = `GET /{id}`。
- 生命周期竞态:管线进行中的档案被退位 / 删除时,管线让位终态,不产出陈旧向量(`test_delete_during_pipeline_wins_and_pipeline_yields`)。
- 空文档、无切片等手动端点的 409 语义在管线内表现为停在 `chunked`,原因进结构化日志。
- BackgroundTasks 为起步实现:同进程、无重试队列,进程重启丢任务;恢复口径是"档案停在的中间态 + 重传幂等",容量需要时再引入队列基础设施。

## 已知代际损耗

- 上传响应不再携带解析结果(element_count / page_count 等),由 `GET /{id}` 下发。
- 评估器固定 `ingestion_async=false`:语料切片参数由数据集显式指定,异步默认参数会打乱指标可比性。
