# 企业 AI 知识库(生产级 RAG)

生产级企业知识库服务的迭代式实践:后端 FastAPI + 自建领域层,框架层以 LangChain 成熟组件当零件库,模型服务统一走硅基流动(OpenAI 兼容)。开发遵循"评估驱动迭代",每个里程碑都产出可复现的前后指标对比。

> 当前进度:**M3 向量化与最小问答链路** + **T06 评估基座** + **T13 生产持久化**(批量向量化 / 三级向量库 / 检索前过滤 / 有据回答 + 引用 / 低分拒答 / 查询日志 / 反馈闭环 / 黄金数据集 + 检索指标 / PostgreSQL + pgvector 六张生产表)+ **T16 运营统计与成本统计**(/stats 一屏 + Token 用量与估算成本);M0–M2 已就位。路线图见 [issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)。

## 快速开始

```bash
uv sync
cp .env.example .env          # 填入 SILICONFLOW_API_KEY;不填也能启动
uv run python -m uvicorn app.asgi:app --reload
```

健康检查:

```bash
curl -H "X-Request-ID: demo-trace-0001" http://127.0.0.1:8000/health
```

```json
{
  "code": "OK",
  "message": "ok",
  "data": {"status": "ok", "version": "0.1.0", "environment": "local", "upstream_configured": true,
           "qa_cache": {"enabled": true, "hits": 12, "misses": 40, "hit_rate": 0.2308}},
  "details": null,
  "trace_id": "demo-trace-0001",
  "timestamp": "2026-10-28T09:00:00Z"
}
```

响应头 `X-Request-ID`、响应体 `trace_id` 与全部日志共享同一个值;调用方传入则透传,缺失或格式非法时服务生成。

### 文档入库(M1 同步 → M7 异步)

上传立即返回任务句柄(文档档案),解析 / 切片 / 向量化默认转后台任务(M7·上,ADR-0012);进度由状态机可查:

```bash
curl -F "file=@退费政策.pdf" http://127.0.0.1:8000/api/v1/documents
```

```json
{
  "code": "OK",
  "data": {
    "id": "9f0b…",
    "file_name": "退费政策.pdf",
    "file_type": "pdf",
    "status": "uploaded"
  }
}
```

- `GET /api/v1/documents/{id}`:状态机位置全程可查
  (`uploaded → parsing → parsed → chunking → chunked → embedding → indexed`,
  任一步失败进入 `failed` 并带 `error_message`);到 `indexed` 即可检索与问答。
- `GET /api/v1/documents/{id}/elements`:解析产物,元素带类型与页码 / 行号 / 标题层级等定位元数据。
- 解析 / 切片失败不静默吞错:档案照常建立,`status=failed`、原因可查,重传同标识文件即重试;上游向量化失败停在 `chunked`,经向量化端点重试;不认识的扩展名返回 `415 UNSUPPORTED_FORMAT`,不生成档案。
- 切分参数排查 / 固定口径:设 `INGESTION_ASYNC=false` 回到请求内同步形态(与异步共用同一服务,行为一致)。
- 落盘位置由 `UPLOAD_DIR` 配置(默认 `data/uploaded_docs`),运行时产物不入库。

### 向量化与检索(M3)

上传后由后台管线自动切片与向量化;也可对停在 `parsed` / `chunked` 的文档手动触发(策略与参数即时生效)。检索支持 top-k 与**检索前**元数据过滤:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents/{id}/chunks \
  -H "Content-Type: application/json" -d '{"strategy": "heading"}'
curl -X POST http://127.0.0.1:8000/api/v1/documents/{id}/embeddings

curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "年假", "top_k": 3, "filters": {"tenant": "acme"}}'
```

- 默认使用 M3 第一层内存向量库(`VECTOR_STORE=memory`,重启即失忆,测试 / CI 口径);
  设 `VECTOR_STORE=chroma`(数据落 `CHROMA_PATH`,默认 `data/chroma`)切换到 Chroma
  持久后端,设 `VECTOR_STORE=pgvector` 切换到 PostgreSQL 16 + pgvector 生产持久化
  (T13;需先 `docker compose up -d db` 看下节):同题同结果,前后指标对比见
  `docs/iterations/0004-t12-vector-store-switch.md` 与 `0005-t13-pgvector.md`。
- 向量库的 `VectorStore` 契约在领域层;换后端只换实现(ADR-0001)。
- 过滤发生在相似度计算之前:被权限条件排除的切片不占 top-k 名额。
- 未配置密钥时返回统一上游错误,文档停在 `chunked` 可直接重试,不落 `failed`。

### 生产持久化(T13,pgvector + Docker Compose)

```bash
docker compose up -d db                                # PostgreSQL 16 + pgvector
uv run python scripts/init_pg.py                       # 幂等建库:扩展 + 六张生产表
VECTOR_STORE=pgvector uv run python -m uvicorn app.asgi:app --reload
```

- 六张表:`documents` / `chunks` / `embeddings` / `query_logs` / `feedbacks` /
  `evaluation_runs`;四仓储(档案 / 切片 / 查询日志 / 反馈)与向量库同切,共用一个
  连接池;建表脚本幂等,可重复执行。
- 全链路(入库 → 问答 → 日志 → 反馈)与"重启容器数据仍在"在真库上测试验证
  (`tests/test_repositories_pg.py`);检索分数与内存基线 / Chroma 同源复算,检索前过滤
  下推 SQL `WHERE` 等价生效(ADR-0011)。
- 评估器同样可切:`uv run python scripts/evaluate.py --vector-store pgvector`,
  `--record-run` 可把报告留档进 `evaluation_runs`(需 `--database-url` 或配置)。
- 无 Docker:保持默认 `VECTOR_STORE=memory` 即可,行为与基线一致;pgvector 模式缺
  `DATABASE_URL` 或库不可达时装配期显式报错,不静默降级。

### 在线问答(M3)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "入职多久可以申请年假？"}'
```

```json
{
  "code": "OK",
  "data": {
    "query_id": "qry_1a2b3c4d5e6f",
    "question": "入职多久可以申请年假？",
    "answer": "根据知识库资料,员工入职满一年后,可以按照公司制度申请年假。",
    "refused": false,
    "refusal_reason": null,
    "top_k": 3,
    "min_score": 0.35,
    "citations": [
      {
        "index": 1,
        "chunk_id": "…",
        "document_id": "…",
        "file_name": "员工手册.md",
        "title": "休假制度",
        "page_number": null,
        "score": 0.79
      }
    ],
    "latency_ms": 4.21,
    "cache_hit": false
  }
}
```

- 资料不足时统一拒答(`refused=true`、`refusal_reason` 为 `no_hits` / `low_score` / `generator_refused`),答案固定为 `根据当前资料无法确认。`,且不返回引用。
- 拒答阈值由 `QA_MIN_SCORE` 配置(默认 `0.35`);默认 `QA_TOP_K=3`。
- 问答答档缓存(T15,ADR-0013):相同问题(空白归一化、忽略大小写,连同 top_k / 阈值 / 过滤 / 生成器口径一致)在 `QA_CACHE_TTL_SECONDS`(默认 300s)内直接复用上次的回答与引用,响应带 `cache_hit=true`,秒级延迟降为亚毫秒且不消耗上游;文档向量化、版本退位与删除即刻清空缓存,答档不落后于知识库变化;命中率随 `qa_cache_hit` / `qa_cache_miss` 日志事件与 `/health` 的 `qa_cache` 字段可查,`QA_CACHE_ENABLED=false` 可整体关闭。
- 查询日志:`GET /api/v1/query-logs?limit=20`(最近优先)与 `GET /api/v1/query-logs/{query_id}`,含原始问题与改写 / 扩展后的问题、命中 / 分数、是否进入证据、拒答原因、生成器种类(`generator`=`rule_based` / `llm`)、Token 用量(`token_usage`)与延迟。
- 回答生成器默认真实 LLM 有据生成(`QA_GENERATOR=llm`,默认模型 `Qwen/Qwen3-8B`,经 `LLM_MODEL` 可换):

  - Prompt 强约束:只依据编号资料回答、不足即统一拒答、绝不编造,回答用 `[编号]` 标注所依据的资料;
  - 上下文为空直接拒答,不触上游;上游超时 / 限流等错误经统一客户端转为 `UPSTREAM_TIMEOUT` / `UPSTREAM_RATE_LIMITED` 等稳定错误码;
  - `QA_GENERATOR=rule` 可切回规则版基线(不触网、零 Token),两种模式在 `generator` 字段上可观测;
  - 真实上游验证:`uv run python scripts/check_upstream.py`(需密钥、会计费)。
- 上游调用限速保护(T15,ADR-0013):`LLM_MIN_INTERVAL_SECONDS` 设置相邻两次上游发送的最小间隔(默认 `0` 不限速;免费档配额紧张时调大),并发请求持锁串行取节拍;上游 429 的 `Retry-After` 头(秒数或 HTTP 日期)被直接采纳为重试等待,无头部时按 `LLM_RETRY_BACKOFF_SECONDS` 指数退避,`upstream_throttled` / `upstream_call_retry` 事件全程留痕。

### 运营统计与成本统计(T16)

`/stats` 一屏看清系统健康度,成本明细按时间段查询:

```bash
curl "http://127.0.0.1:8000/api/v1/stats?bucket=day"
curl "http://127.0.0.1:8000/api/v1/stats/cost?start=2026-10-01T00:00:00Z&end=2026-11-01T00:00:00Z"
```

```json
{
  "code": "OK",
  "data": {
    "document_count": 12,
    "indexed_document_count": 12,
    "chunk_count": 268,
    "qa": {"query_count": 143, "refused_count": 21, "refusal_rate": 0.146853,
           "avg_latency_ms": 812.44,
           "token_usage": {"prompt_tokens": 98012, "completion_tokens": 15430, "total_tokens": 113442}},
    "cost": {"prompt_cost": 0.196024, "completion_cost": 0.09258, "total_cost": 0.288604, "currency": "CNY"},
    "buckets": [{"bucket_start": "2026-10-30T00:00:00Z", "bucket_end": "2026-10-31T00:00:00Z", "qa": {}}],
    "start": null, "end": null, "bucket_kind": "day"
  }
}
```

- **核心指标**:文档数 / 已索引文档数 / 切片数 / 问答量 / 拒答数 / 拒答率 / 平均延迟 / Token 用量;分桶时间序列按 `bucket=hour|day` 切分(UTC 自然边界,没有问答的桶不出现)。
- **区间口径**:起点闭区间 / 终点开区间,`start` / `end` 可选(缺省即"至今全部");起点不早于终点返回 400 `BAD_REQUEST`。
- **拒答率是线上实际拒答占比**(拒答数 / 问答量),问答量为 0 时为 `null`(无样本 ≠ 0);与评估器的"拒答正确率"是两种口径,不可互相替代。
- **估算成本**:Token 用量按配置单价(元 / 千 Token)折算,`COST_PROMPT_PRICE_PER_1K` / `COST_COMPLETION_PRICE_PER_1K` 配置;换模型只改配置。这是**估算**而非上游账单,且当前只覆盖回答生成步的 Token(embedding / 规划 / 重排的用量只在结构化日志里),属全链路成本的下界;答档缓存命中时零上游消耗,日志记零 Token,不会重复计入。
- **统计来自持久化数据**:pgvector 模式读生产表,重启容器不丢;memory 与 chroma 模式的统计随进程失忆(数据仓储仍是进程内存实现,chroma 只持久化**向量**,不持久化统计)。详见 [ADR-0014](docs/adr/0014-operational-stats-and-cost-estimation.md)。

### 检索评估(T06)

一条命令跑黄金数据集,产出 Recall@K / MRR / NDCG@K 与逐用例报告(教材第 44 章):

```bash
uv run python scripts/evaluate.py
```

- 数据集:`evaluation/golden_dataset.yaml`(v1,26 条;含 2 条拒答、1 条租户权限边界、2 条多切片用例);语料在 `evaluation/corpus/`。
- 报告:`docs/iterations/0001-t06-retrieval-baseline.md`(人读)与同名 `.json`(CI / 后续对比);逐用例列出预期锚点、`文档#index`、top-k 命中与指标。
- 默认在进程内启动应用并注入 1024 维确定性向量替身,**无密钥环境(CI)可跑**;报告会写明 `embedder=deterministic-lexical-1024d`。
- 评估走 HTTP 主缝(上传 → 切片 → 向量化 → 检索),与真实用户路径一致;不用服务层内部捷径。
- 连接真实服务:`uv run python scripts/evaluate.py --base-url http://127.0.0.1:8000 --min-score 0.35 --embedder-label BAAI/bge-m3`;外部模式必须显式给出服务端口径,且建议对专用 / 重启后的服务跑,避免重复入库污染指标。
- 后续迭代用 `--iteration 0002 --slug t07-advanced-retrieval --run-name "T07 高级检索"` 生成新报告,不要覆盖基线。
- 预期切片用「文档 + 文内唯一锚点」定位,评估时解析成真实 `chunk_id` 参与计算;报告中用稳定的 `文档#index` 定位,随机 uuid 不写进数据集与报告。

## 工程约定

- **统一响应信封**:`code / message / data / details / trace_id / timestamp`,成功与失败同形。
- **统一异常体系**:业务异常继承 `AppError`,携带稳定错误码与 HTTP 状态;意外异常统一转换为 `INTERNAL_ERROR`(500),堆栈只进日志。
- **统一 LLM 客户端**:`chat / embed / rerank` 三类调用共享超时、指数退避重试、错误映射与用量 / 延迟结构化日志;更换供应商只改配置。
- **密钥只走环境变量**:`.env` 已被 gitignore,仓库只提交 `.env.example` 占位符。未配置密钥时服务可正常启动,相关调用返回 `UPSTREAM_NOT_CONFIGURED`。

### 错误码

| 错误码 | HTTP | 含义 |
| --- | --- | --- |
| `INTERNAL_ERROR` | 500 | 未预期的服务内部错误 |
| `BAD_REQUEST` | 400 | 请求参数不合法(如统计区间起点不早于终点) |
| `VALIDATION_ERROR` | 422 | 请求校验失败 |
| `NOT_FOUND` | 404 | 资源 / 接口不存在 |
| `METHOD_NOT_ALLOWED` | 405 | 请求方法不允许 |
| `UNSUPPORTED_FORMAT` | 415 | 上传了不支持的文档格式 |
| `UPSTREAM_NOT_CONFIGURED` | 503 | 未配置上游密钥 |
| `UPSTREAM_AUTH_FAILED` | 502 | 上游鉴权失败 |
| `UPSTREAM_RATE_LIMITED` | 429 | 上游限流 |
| `UPSTREAM_TIMEOUT` | 504 | 上游超时 |
| `UPSTREAM_UNAVAILABLE` | 503 | 上游 5xx / 连接失败 |
| `UPSTREAM_BAD_RESPONSE` | 502 | 上游响应格式异常或 4xx |

## 开发

```bash
uv run pytest                   # 全量测试,不触网
uv run ruff check .             # lint
uv run python scripts/evaluate.py         # 黄金数据集检索指标(默认无密钥)
uv run python scripts/benchmark_ingestion.py # 入库形态上传阻塞时长(同步 vs 异步)
uv run python scripts/benchmark_qa_cache.py  # 同题二次询问延迟(缓存关 vs 开,T15)
uv run python scripts/benchmark_stats.py   # 统计口径与成本折算核对(T16)
uv run python scripts/check_upstream.py   # 真实上游连通性(需密钥,会计费)
```

测试从预先约定的接缝进入:

- **HTTP API 层**:健康检查、错误映射、trace_id 一致性、堆栈不外泄;文档解析链路(五种格式、失败状态、落盘配置);异步入库管线(上传立即返回、状态机进度、失败重试);向量化 / 检索;在线问答(有据回答 + 引用、低分拒答、查询日志),上游以确定性假向量与可注入生成器替身。
- **评估主缝**:黄金数据集经同一组 HTTP 端点入库并检索,断言逐用例可追溯、权限边界与拒答用例生效、报告可重复渲染;无密钥、不触网。
- **统一 LLM 客户端的窄接缝**:假传输层注入超时 / 429 / 5xx,验证错误映射与用量解析。

## 目录结构

```
app/
  asgi.py                    # ASGI 入口(只装配,供 uvicorn)
  main.py                    # 应用工厂 create_app
  api/routes/                # HTTP 路由(健康检查 / 文档)
  core/                      # 配置 / 异常 / 信封 / 日志 / trace_id / 异常处理器
  domain/                    # 领域层:ParsedDocument schema / 状态机 / 仓储接口(权威契约)
  services/                  # 应用服务:入库编排 / 落盘存储
  repositories/              # 仓储实现(内存基线 / Chroma / PG 生产持久化)
  integrations/              # 实现层零件
    llm/                     #   统一 LLM 客户端与可注入传输层
    parsing/                 #   五种格式解析器与工厂(ADR-0002)
evaluation/                  # 黄金数据集(v1 YAML)与评估语料
tests/                       # 按接缝组织的测试
scripts/evaluate.py          # 检索指标评估器(默认无密钥)
scripts/benchmark_ingestion.py # 入库形态上传阻塞时长基准(同步 vs 异步)
scripts/benchmark_qa_cache.py  # 同题二次询问延迟基准(缓存关 vs 开,T15)
scripts/benchmark_stats.py    # 运营统计与成本折算核对基准(T16)
scripts/init_pg.py           # 幂等建表 / 迁移(pgvector 生产持久化)
scripts/check_upstream.py    # 需要密钥的连通性脚本
docker-compose.yml           # PostgreSQL 16 + pgvector 一键拉起
docs/adr/                    # 架构决策记录
docs/iterations/             # 指标基线 / 前后对比报告
GLOSSARY.md                  # 领域术语表(唯一权威词汇)
```

## 文档

- [GLOSSARY.md](GLOSSARY.md):领域术语
- [ADR-0001](docs/adr/0001-langchain-parts-domain-layer.md):自建领域层,LangChain 组件当零件库
- [ADR-0002](docs/adr/0002-lightweight-parsing-libs.md):文档解析用轻量解析库,不引入 langchain-community loader
- [ADR-0003](docs/adr/0003-failure-semantics-and-memory-vector-baseline.md):向量化失败不落 failed;内存向量库为 M3 基线
- [ADR-0004](docs/adr/0004-grounded-answer-refusal-baseline.md):有据回答基线:规则生成器、低分拒答与内存查询日志
- [ADR-0005](docs/adr/0005-evaluation-foundation.md):评估基座:锚点定位、API 主缝与无密钥检索指标
- [ADR-0009](docs/adr/0009-document-supersession-and-deletion.md):文档版本退位与删除:清理先行、状态机后置
- [ADR-0011](docs/adr/0011-pgvector-production-persistence.md):生产持久化切 pgvector;六张生产表;无 Docker 回退口径
- [ADR-0012](docs/adr/0012-async-ingestion-background-tasks.md):异步 ingestion:上传即时返回任务句柄;同步形态显式配置
- [ADR-0013](docs/adr/0013-qa-answer-cache-and-upstream-throttle.md):问答答档缓存与上游限速:进程内快答档,检索面变化即整体失效
- [ADR-0014](docs/adr/0014-operational-stats-and-cost-estimation.md):运营统计与成本统计:查询日志为唯一统计来源,成本按配置单价估算
- [T06 检索指标基线](docs/iterations/0001-t06-retrieval-baseline.md):24 条黄金用例,含逐用例追溯与后续对比约定
- 里程碑与验收标准:[issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)
