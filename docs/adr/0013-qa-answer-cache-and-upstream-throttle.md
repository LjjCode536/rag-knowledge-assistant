# ADR-0013: 问答答档缓存与上游限速——进程内快答档,检索面变化即整体失效

**Status**: accepted

## Context

M7·中(spec,issue #16)要求:相同问题的重复咨询复用结果,降低延迟与上游消耗;
对上游调用加限速保护,免费档配额不被密集并发打爆;命中 / 未命中可观测。

此前 `/api/v1/chat` 每请求全链路走"检索(含规划 / 重排,均为上游 LLM 调用)→
生成(上游 LLM 调用)"。知识库内容只在三处变化:向量化入库(`POST /{id}/embeddings`,
整组替换)、版本退位(上传同标识新版本,旧版本退出检索面)、删除——检索面在
两次变化之间是**事实不变量**,相同问题(连同 top_k / 阈值 / 过滤 / 生成器口径)
的答案在这段窗口内必然相同。同时上游侧只有单请求重试,没有跨请求发送节拍,
上游 429 返回的 `Retry-After` 指示也被忽略。

## Decision

- **答档缓存放问答编排层,放`AnswerService.ask`**:`app/services/qa_cache.py` 提供
  进程内 LRU(容量 `QA_CACHE_MAXSIZE`)+ TTL(`QA_CACHE_TTL_SECONDS`)缓存;
  键为"归一化问题(空白折叠、casefold)+ effective top_k + min_score + filters +
  生成器种类"的 SHA-256 摘要——口径不同即不同键,绝不跨口径共享答档。
- **缓存的是"完成的 QueryLog + Citations"而非流式片段**:命中时深拷贝成新的查询
  日志(新 `query_id` / `trace_id` / 重新实测 `latency_ms`)入库留痕,调用方日志
  可追溯性与未命中形态完全一致;生成链路(检索 / 规划 / 重排 / 驱动 LLM)整段跳过,
  响应带 `cache_hit=true`。
- **失效 = 检索面变化的三个消号点**:`EmbeddingService.embed_document` 成功、
  `DocumentLifecycleService` 的 supersede / delete 清理成功,即整体 `clear`(事件
  `qa_cache_invalidated`)。不做文档维度的增量失效——进程内存形态下整体清空,
  代价是一次冷重建;换"答档永不落后于知识库"的简单正确性。
- **限速保护 = 客户端发送节拍,收口在 `UnifiedLLMClient._post`**:`LLM_MIN_INTERVAL_SECONDS`
  (默认 0 = 不限速)约束相邻两次上游发送(含同一请求的重试)的最小间隔;
  单客户端内 asyncio 锁串行取时隙,时钟(`clock` )与 sleep 一样可注入,离线可测。
- **429 退避尊重服务端指示**:上游响应带 `Retry-After`(秒数或 HTTP 日期)时直接
  采纳为重试等待,无头部 / 解析失败 / 非法值回退 `llm_retry_backoff_seconds` 指数
  退避(原行为);传输层 `TransportResponse` 增加 `headers` 透传。
- **可观测 = 事件 + 统计**:命中 / 未命中事件 `qa_cache_hit` / `qa_cache_miss`
  (带累计命中率),响应视图 `cache_hit` 字段,`/health` 增 `qa_cache` 统计块
  (enabled / hits / misses / hit_rate),限速等待记 `upstream_throttled`。

## Consequences

- **缓存默认开启**(`QA_CACHE_ENABLED=true`):评估器(`scripts/evaluate.py`)不走
  `AnswerService`,检索指标不受影响;`conftest` 各测试应用独立装配,默认 TTL /
  容量内不影响既有契约。关闭即回到原全链路形态。
- **拒答同样被缓存**:拒答由检索命中 / 阈值与生成器行为决定,同窗口内同样是事实
  不变量;文档变化的三处消号保证了知识库更新后拒答结论即时失效。
- **命中日志的中立性**:命中复制的查询日志里,`question` 为本次请求原文,
  `latency_ms` 为本次实测;`token_usage` / `rewritten_question` / `expanded_questions`
  / `hits` 保留的是首发请求口径——它们记录"这一答档的来源",不是本次请求的
  重算结果,读报表时按答档溯源口径理解。
- **多实例 / 重启语义**:进程内缓存随进程失忆(与 memory 向量库口径一致);多
  实例各自维护答档与统计,命中率跨实例不可聚合——生产需要跨实例聚合时,接入
  共享存储是缓存类设施的自然演进点,`AnswerCache` 的窄接口可替换而不动编排层。
- **限速节拍是客户端侧自保**:它约束本进程对上游的发送速率,不承诺在多实例 /
  多客户端下的全局配额;上游 429 的服务端公正裁决仍由重试退避承接。
- 结构化日志新增:`qa_cache_hit` / `qa_cache_miss` / `qa_cache_invalidated` /
  `upstream_throttled`;`upstream_call_retry` 增加 `delay_seconds` / `retry_after`
  字段;`/health` 响应 `data` 增加 `qa_cache` 块(调用方按需读取)。
