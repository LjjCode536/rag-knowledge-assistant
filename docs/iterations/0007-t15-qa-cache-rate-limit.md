# 迭代 0007:T15 问答答档缓存与上游限速(M7·中)

**工单**:issue #16(T15,父 #1) · **ADR**:ADR-0013 · **基准脚本**:`uv run python scripts/benchmark_qa_cache.py`

## 背景与可控指标

在线问答此前每次请求都走"检索 → 生成"全链路:相同问题重复咨询时,延迟和上游
Token 消耗按重复次数线性累积;上游侧除单请求重试外没有跨请求限速,免费档配额
可能被密集并发瞬间打爆。本迭代的可控指标:

1. **二次同题延迟**与回答一致性(缓存开 / 关对比,`cache_hit` 字段与日志可观测);
2. 上游调用速率保护与 429 退避行为(单测口径);
3. 缓存命中率可观测(结构化日志 + `/health`)。

## 做法

- **答档缓存**(`app/services/qa_cache.py`):进程内 LRU + TTL。键覆盖全部影响回答的
  过程口径——归一化问题(空白折叠、忽略大小写)/ top_k / 拒答阈值 / 检索前过滤 /
  生成器种类,SHA-256 定长摘要;存"完成的 QueryLog + 引用"。命中时复制成新的
  查询日志(新 query_id / trace_id / 实测延迟)入库留痕,生成链路(检索、规划、
  重排、LLM 调用)整段跳过。
- **失效口径**:知识库检索面变化即整体清空——文档向量化(`document_embedded`)、
  版本退位(`document_superseded`)、删除(`document_deleted`)三处事件消号,答档
  不落后于知识库更新。增量失效(按文档维度)留待真实需要再引入。
- **上游限速保护**(`UnifiedLLMClient`):`LLM_MIN_INTERVAL_SECONDS` 设置相邻两次
  上游发送(含同一请求的重试)的最小间隔,持锁串行取节拍,并发请求排队等时隙;
  时钟注入,默认关闭(0),不影响既有契约测试。
- **429 退避**:遵循上游 `Retry-After` 头(秒数或 HTTP 日期,解析失败回退指数退避),
  退避等待量录入 `upstream_call_retry` 日志(`delay_seconds` / `retry_after` 字段)。
- **可观测**:命中 / 未命中事件 `qa_cache_hit` / `qa_cache_miss`(带累计命中率),
  缓存失效事件 `qa_cache_invalidated`,响应视图新增 `cache_hit` 字段,`/health` 增
  `qa_cache` 统计(enabled / hits / misses / hit_rate)。

## 峰值指标(benchmark_qa_cache,标准配置全开)

### 运行环境

- 进程内真实 uvicorn(临时端口),端到端延迟自含 HTTP 与事件循环成本。
- 60 节员工手册 md 语料,heading 切片(60 切片)入库;确定性向量替身;
  `QA_GENERATOR=rule`、检索基线路径(规划 / 重排关闭):对比口径为纯检索 + 生成本地成本。

### 运行结果(2026-10-30,同题 40 次 ×2 形态)

| 形态 | 平均 | 中位 | 最快 | 最慢 |
| --- | ---: | ---: | ---: | ---: |
| 关闭答档缓存 | 4.96 ms | 4.67 ms | 3.65 ms | 14.24 ms |
| 开启答档缓存 | 2.75 ms | 2.58 ms | 1.86 ms | 6.67 ms |

- 同题热询问平均 `4.96 ms → 2.65 ms`(约 1.9x),40 次同题回答逐字一致;
  本地替身路径下手册索引极小、生成零 Token,延迟基线本身亚毫秒级,1.9x 是架构
  层面的保守下界。**生产口径**(`QA_GENERATOR=llm`):缓存命中额外省去同款的
  一次 embedding + 规划 + 重排 + LLM 生成四段上游往返,单次同提问答复通常在
  数百 ms ~ 秒级,命中答档 ≈ 亚毫秒;同时检索与上游的 Token 消耗归零。
- 新生 / 过程正确性:命中率、口径分键(不同 top_k / 过滤不共享)、TTL 过期、
  容量淘汰、三处失效(向量化 / 退位 / 删除)与 `/health` 统计都有独立用例。

## 测试

- `tests/test_qa_cache.py`:缓存本体窄接缝(8 例)。
- `tests/test_chat_cache_api.py`:HTTP 主接缝 6 例——同题二次命中一致性、空白归一化、
  禁用 / 口径分键、向量化 / 删除 / 退位三失效与日志事件。
- `tests/test_llm_client.py` 新增:跨请求限速节拍(注入时钟)、默认不限速不等待、
  429 尊重数字与 HTTP 日期 `Retry-After`、无头部维持指数退避。
- `tests/test_health.py`:`/health` 统计形状与禁用形态。
- 全量:`uv run pytest` 293 例通过,`uv run ruff check .` 干净。
