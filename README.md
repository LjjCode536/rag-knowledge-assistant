# 企业 AI 知识库(生产级 RAG)

生产级企业知识库服务的迭代式实践:后端 FastAPI + 自建领域层,框架层以 LangChain 成熟组件当零件库,模型服务统一走硅基流动(OpenAI 兼容)。前端是独立的 Vue 3 SPA,置于 `frontend/` 子目录。开发遵循"评估驱动迭代",每个里程碑都产出可复现的前后指标对比。

> 当前进度:**M3 向量化与最小问答链路** + **T06 评估基座** + **T13 生产持久化**(批量向量化 / 三级向量库 / 检索前过滤 / 有据回答 + 引用 / 低分拒答 / 查询日志 / 反馈闭环 / 黄金数据集 + 检索指标 / PostgreSQL + pgvector 六张生产表)+ **T16 运营统计与成本统计**(/stats 一屏 + Token 用量与估算成本)+ **T17 知识库文档列表端点** + **T18–T22 前端工作台**(Vue 3 SPA:登录 + 六个业务页;API 客户端层 + 组件测试)+ **身份、租户硬隔离与文档级授权**(短期 JWT 登录 / 必填授权范围 / 文档默认不可读与显式授权 / 审计与平台管理员);M0–M2 已就位。路线图见 [issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)。

## 快速开始

```bash
uv sync
cp .env.example .env          # 填入 SILICONFLOW_API_KEY;不填也能启动
```

登录需要主体账号。账号只落 PostgreSQL(内存模式不建号,仅供测试夹具),所以本地演示按生产持久化形态启动:

```bash
docker compose up -d db
uv run python scripts/init_pg.py                 # 幂等建表(含主体 / 授权 / 审计)
SEED_MEMBER_PASSWORD='...' SEED_ADMIN_PASSWORD='...' \
  uv run python scripts/seed_principals.py --yes  # 显式建种子账号,启动绝不自动建号
VECTOR_STORE=pgvector uv run python -m uvicorn app.asgi:app --reload
```

种子脚本的默认账号(用户名 / 租户 / 角色 / 部门,均可用 `SEED_<NAME>_*` 覆盖):

| 用户名 | 租户 | 角色 | 部门 |
| --- | --- | --- | --- |
| `member` | `default` | `member` | 研发部 |
| `admin` | `default` | `admin` | 平台部 |
| `member-other` | `other` | `member` | 市场部 |
| `operator` | `platform` | `platform_operator` | — |

登录换取短期 JWT(默认 1 小时,无 refresh token,过期重新登录):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"member","password":"你设置的口令"}'
```

响应里的 `access_token` 之后以 `Authorization: Bearer <token>` 发送;`GET /api/v1/auth/me` 只读返回主体。租户、角色与部门**全部来自 token**:普通端点不接受 `tenant` 参数(携带即 403),跨租户只能走管理端点,由平台管理员显式选择租户并逐次写审计。OpenAPI(`/openapi.json`)对受保护端点声明 `HTTPBearer`。

健康检查(公开端点,无需登录):

```bash
curl -H "X-Request-ID: demo-trace-0001" http://127.0.0.1:8000/health
```

```json
{
  "code": "OK",
  "message": "ok",
  "data": {"status": "ok", "version": "0.1.0", "environment": "local", "upstream_configured": true},
  "details": null,
  "trace_id": "demo-trace-0001",
  "timestamp": "2026-10-28T09:00:00Z"
}
```

响应头 `X-Request-ID`、响应体 `trace_id` 与全部日志共享同一个值;调用方传入则透传,缺失或格式非法时服务生成。

### 前端工作台(T18)

独立的 Vue 3 SPA,置于 `frontend/` 子目录,**不进入 Python 依赖体系**(`uv` / `pyproject.toml` 不感知前端):

```bash
cd frontend
pnpm install
pnpm dev            # http://localhost:5173
```

- **后端零改动、零 CORS**:开发期靠 Vite dev server 的 proxy 把 `/api` 与
  `/health` 转发到后端 `127.0.0.1:8000`,浏览器视角下前端与接口同源;生产期用
  同一域名反代同时服务静态产物与接口,后端仍然不需要 CORS。前端**不挂进
  FastAPI**(不 `StaticFiles`、不 `Jinja2Templates`)。
- **登录页 + 六个业务页**:问答(默认首页)/ 知识库文档 / 检索 / 运营统计 / 查询日志 / 反馈;
  左侧边栏导航,窄屏侧边栏折叠为抽屉。
- **请求收口一处**:所有 HTTP 调用经 `api/` 层唯一的底层请求函数(发请求 →
  拆响应信封 → 按错误码抛类型化错误 → 带上 `trace_id`)。**页面组件只调
  `api/` 层的函数,不得直接发请求。**
- **身份来自登录**:前端有登录页,口令换短期 JWT(内存 + `sessionStorage`,演示级存储);token 由 `api/client.ts` 统一注入每个请求,401 即清理凭证并回登录页。租户、角色与部门只读展示,**租户切换器已移除**;隐藏按钮只是体验,权限始终由服务端强制(ADR-0020 / 0021)。
- **文档详情页的授权清单**:所有者与租户管理员可查看 / 添加 / 移除用户、部门或整租户授权,并"发布到租户";普通成员只读。
- **如实呈现后端边界**:`/chat` 无流式,第一版用"请求完成后打字机动画"的假流式。
- 详见 [ADR-0015](docs/adr/0015-frontend-stack-and-boundary.md)、[ADR-0020](docs/adr/0020-identity-and-tenant-isolation.md) 与 [`frontend/README.md`](frontend/README.md)。

### 文档入库(M1 同步 → M7 异步)

> 以下示例都假设已登录:`$TOKEN` 为 `POST /api/v1/auth/login` 返回的 `access_token`,所有受保护端点都要带 `Authorization: Bearer $TOKEN`。

上传立即返回任务句柄(文档档案),解析 / 切片 / 向量化默认转后台任务(M7·上,ADR-0012);进度由状态机可查:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -F "file=@退费政策.pdf" http://127.0.0.1:8000/api/v1/documents
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

- 租户、文档所有者与部门由服务端从 token 主体写入,表单不接受 `tenant`(携带即 422);新文档默认只有所有者与租户管理员可见。
- `GET /api/v1/documents/{id}`:状态机位置全程可查
  (`uploaded → parsing → parsed → chunking → chunked → embedding → indexed`,
  任一步失败进入 `failed` 并带 `error_message`);到 `indexed` 即可检索与问答。跨租户或不存在的 id 一律 404,不泄露存在性。
- `GET /api/v1/documents/{id}/elements`:解析产物,元素带类型与页码 / 行号 / 标题层级等定位元数据。
- `GET /api/v1/documents`:知识库文档清单(T17),按创建时间倒序,支持 `status` /
  `keyword` / `department` / `doc_type` 筛选与 `limit` / `offset` 分页,响应给
  `total`。可见范围按主体授权收敛;**默认只列现行版本**(退位的旧版本与已删除的文档不进清单),`include_history=true` 可查历史。
- 写路径(切片 / 向量化 / 删除 / 授权)只允许文档所有者或租户管理员;普通成员越界返回 403 或 404。
- 解析 / 切片失败不静默吞错:档案照常建立,`status=failed`、原因可查,重传同标识文件即重试;上游向量化失败停在 `chunked`,经向量化端点重试;不认识的扩展名返回 `415 UNSUPPORTED_FORMAT`,不生成档案。
- 切分参数排查 / 固定口径:设 `INGESTION_ASYNC=false` 回到请求内同步形态(与异步共用同一服务,行为一致)。
- 落盘位置由 `UPLOAD_DIR` 配置(默认 `data/uploaded_docs`),运行时产物不入库。

### 向量化与检索(M3)

上传后由后台管线自动切片与向量化;也可对停在 `parsed` / `chunked` 的文档手动触发(策略与参数即时生效)。检索支持 top-k 与**检索前**元数据过滤:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/documents/{id}/chunks \
  -H "Content-Type: application/json" -d '{"strategy": "heading"}'
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/documents/{id}/embeddings

curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "年假", "top_k": 3, "filters": {"department": "研发部"}}'
```

- **检索只发生在授权范围内**:租户是硬边界,文档级允许集合在检索前展开;收窄条件(部门 / 文档类型 / 文档集合)只能在授权范围内收窄,越界返回 `403 SCOPE_VIOLATION`,不静默裁剪。空允许集合表示一条都查不到,绝不表示"不限制"。

- 默认使用 M3 第一层内存向量库(`VECTOR_STORE=memory`,重启即失忆,测试 / CI 口径);
  设 `VECTOR_STORE=chroma`(数据落 `CHROMA_PATH`,默认 `data/chroma`)切换到 Chroma
  持久后端,设 `VECTOR_STORE=pgvector` 切换到 PostgreSQL 16 + pgvector 生产持久化
  (T13;需先 `docker compose up -d db` 看下节):同题同结果,前后指标对比见
  `docs/iterations/0004-t12-vector-store-switch.md` 与 `0005-t13-pgvector.md`。
- 向量库的 `VectorStore` 契约在领域层;换后端只换实现(ADR-0001)。
- 每个向量随库带来源身份(`embedding_model` / `embedding_version` /
  `embedding_dimension`):检索只与同一身份的向量互算,同维度换模型不会被静默混算
  (ADR-0019)。换模型必须整库重嵌;历史行先跑
  `uv run python scripts/backfill_embedding_identity.py`(默认 dry-run,确认后加 `--apply`)。
- 过滤发生在相似度计算之前:被权限条件排除的切片不占 top-k 名额。
- 未配置密钥时返回统一上游错误,文档停在 `chunked` 可直接重试,不落 `failed`。

### 生产持久化(T13,pgvector + Docker Compose)

```bash
docker compose up -d db                                # PostgreSQL 16 + pgvector
uv run python scripts/init_pg.py                       # 幂等建库:扩展 + 九张生产表
VECTOR_STORE=pgvector uv run python -m uvicorn app.asgi:app --reload
```

- 九张表:`documents` / `chunks` / `embeddings` / `query_logs` / `feedbacks` /
  `evaluation_runs`,以及身份与授权的 `principals` / `document_grants` /
  `audit_events`;四仓储(档案 / 切片 / 查询日志 / 反馈)与向量库、主体 / 授权 /
  审计仓储同切,共用一个连接池;建表脚本幂等,可重复执行。
- 种子账号只落 PostgreSQL:显式运行 `scripts/seed_principals.py --yes`;服务启动绝不自动建号(见「快速开始」)。
- 全链路(入库 → 问答 → 日志 → 反馈)与"重启容器数据仍在"在真库上测试验证
  (`tests/test_repositories_pg.py`);检索分数与内存基线 / Chroma 同源复算,检索前过滤
  下推 SQL `WHERE` 等价生效(ADR-0011)。
- 评估器同样可切:`uv run python scripts/evaluate.py --vector-store pgvector`,
  `--record-run` 可把报告留档进 `evaluation_runs`(需 `--database-url` 或配置)。
- 无 Docker:保持默认 `VECTOR_STORE=memory` 即可跑检索与评估,但**内存模式没有登录账号**(仅供测试夹具);pgvector 模式缺
  `DATABASE_URL` 或库不可达时装配期显式报错,不静默降级。

### 在线问答(M3)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
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
- 问答答档缓存(T15,ADR-0013):相同问题(空白归一化、忽略大小写,连同 top_k / 阈值 / 生成器口径一致)在 `QA_CACHE_TTL_SECONDS`(默认 300s)内直接复用上次的回答与引用,响应带 `cache_hit=true`,秒级延迟降为亚毫秒且不消耗上游;缓存键包含**授权后的有效范围**(租户 + 主体 + 允许集合),不同租户 / 不同主体不共享答档,授权授予或撤销、文档向量化、版本退位与删除即刻整体清空缓存;命中率随 `qa_cache_hit` / `qa_cache_miss` 日志事件与管理员诊断端点 `/api/v1/admin/diagnostics` 的 `qa_cache` 字段可查(公开 `/health` 不再暴露该诊断信息),`QA_CACHE_ENABLED=false` 可整体关闭。
- 查询日志:`GET /api/v1/query-logs?limit=20`(最近优先)与 `GET /api/v1/query-logs/{query_id}`,含原始问题与改写 / 扩展后的问题、命中 / 分数、是否进入证据、拒答原因、生成器种类(`generator`=`rule_based` / `llm`)、Token 用量(`token_usage`)与延迟。日志带显式租户与主体列,成员只看到自己的提问,租户管理员看本租户,统计同样只统计本租户。
- 回答生成器默认真实 LLM 有据生成(`QA_GENERATOR=llm`,默认模型 `Qwen/Qwen3-8B`,经 `LLM_MODEL` 可换):

  - Prompt 强约束:只依据编号资料回答、不足即统一拒答、绝不编造,回答用 `[编号]` 标注所依据的资料;
  - 上下文为空直接拒答,不触上游;上游超时 / 限流等错误经统一客户端转为 `UPSTREAM_TIMEOUT` / `UPSTREAM_RATE_LIMITED` 等稳定错误码;
  - `QA_GENERATOR=rule` 可切回规则版基线(不触网、零 Token),两种模式在 `generator` 字段上可观测;
  - 真实上游验证:`uv run python scripts/check_upstream.py`(需密钥、会计费)。
- 上游调用限速保护(T15,ADR-0013):`LLM_MIN_INTERVAL_SECONDS` 设置相邻两次上游发送的最小间隔(默认 `0` 不限速;免费档配额紧张时调大),并发请求持锁串行取节拍;上游 429 的 `Retry-After` 头(秒数或 HTTP 日期)被直接采纳为重试等待,无头部时按 `LLM_RETRY_BACKOFF_SECONDS` 指数退避,`upstream_throttled` / `upstream_call_retry` 事件全程留痕。

### 运营统计与成本统计(T16)

`/stats` 一屏看清系统健康度,成本明细按时间段查询:

```bash
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/v1/stats?bucket=day"
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/v1/stats/cost?start=2026-10-01T00:00:00Z&end=2026-11-01T00:00:00Z"
```

- 统计与成本端点只对租户 `admin` 开放,数字只统计本租户;平台管理员经管理端点显式选择租户并留痕。

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
- **统计来自持久化数据**:pgvector 模式读生产表,重启容器不丢(真库验证:`tests/test_stats_pg.py`);memory 与 chroma 模式的统计随进程失忆(数据仓储仍是进程内存实现,chroma 只持久化**向量**,不持久化统计)。详见 [ADR-0014](docs/adr/0014-operational-stats-and-cost-estimation.md)。

### 检索评估(T06)

一条命令跑黄金数据集,产出 Recall@K / MRR / NDCG@K 与逐用例报告(教材第 44 章):

```bash
uv run python scripts/evaluate.py
```

- 数据集:`evaluation/golden_dataset.yaml`(v1,26 条;含 2 条拒答、1 条租户权限边界、2 条多切片用例);语料在 `evaluation/corpus/`。
- **门禁**:`uv run python scripts/evaluate.py --min-score 0.07 --check`(与 CI 同口径;确定性替身的拒答操作点是 0.07,生产 bge-m3 用 0.35)。任一指标跌破下限即 exit 1,其中权限边界通过率要求 100%。
- 报告:`docs/iterations/0001-t06-retrieval-baseline.md`(人读)与同名 `.json`(CI / 后续对比);逐用例列出预期锚点、`文档#index`、top-k 命中与指标。
- 默认在进程内启动应用并注入 1024 维确定性向量替身,**无密钥环境(CI)可跑**;报告会写明 `embedder=deterministic-lexical-1024d`。
- 评估走 HTTP 主缝(上传 → 切片 → 向量化 → 检索),与真实用户路径一致;不用服务层内部捷径。
- 连接真实服务:`uv run python scripts/evaluate.py --base-url http://127.0.0.1:8000 --min-score 0.35 --embedder-label BAAI/bge-m3`;外部模式必须显式给出服务端口径,且建议对专用 / 重启后的服务跑,避免重复入库污染指标。评估器按租户签发真实 JWT,外部服务需与本地共用 `AUTH_JWT_SECRET` / issuer / audience,否则以 401 明确失败(没有 `EVAL_BYPASS`)。
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
| `BAD_REQUEST` | 400 | 请求参数语义不合法(如统计区间起点不早于终点) |
| `UNAUTHENTICATED` | 401 | 未携带、无效或已过期的访问凭证 |
| `SCOPE_VIOLATION` | 403 | 收窄条件或租户参数超出主体授权范围(不静默裁剪) |
| `NOT_FOUND` | 404 | 资源 / 接口不存在;跨租户对象同样 404,不泄露存在性 |
| `METHOD_NOT_ALLOWED` | 405 | 请求方法不允许 |
| `INVALID_DOCUMENT_STATE` | 409 | 文档当前状态不允许该操作 |
| `UNSUPPORTED_FORMAT` | 415 | 上传了不支持的文档格式 |
| `VALIDATION_ERROR` | 422 | 请求校验失败(含请求体 / 表单携带未声明字段,如 `tenant`) |
| `EMBEDDING_DIMENSION_MISMATCH` | 500 | 向量维度与索引声明的维度不一致 |
| `AUDIT_WRITE_FAILED` | 500 | 变更类操作的审计写入失败,业务写随之失败 |
| `DOCUMENT_PARSE_FAILED` / `DOCUMENT_CHUNK_FAILED` | 500 | 解析 / 切片失败(落在文档 `failed` 状态,原因可查) |
| `DOCUMENT_SUPERSEDE_FAILED` / `DOCUMENT_DELETE_FAILED` | 500 | 版本退位 / 删除连带清理失败,档案原状保留,可重试 |
| `UPSTREAM_NOT_CONFIGURED` | 503 | 未配置上游密钥 |
| `UPSTREAM_AUTH_FAILED` | 502 | 上游鉴权失败 |
| `UPSTREAM_RATE_LIMITED` | 429 | 上游限流 |
| `UPSTREAM_TIMEOUT` | 504 | 上游超时 |
| `UPSTREAM_UNAVAILABLE` | 503 | 上游 5xx / 连接失败 |
| `UPSTREAM_BAD_RESPONSE` | 502 | 上游响应格式异常或 4xx |

### 安全边界验收(ADR-0020 / 0021)

- **身份**:登录换短期 JWT;`/api/v1/auth/me` 只读返回主体;缺失 / 过期 / 篡改 / 非 Bearer 凭证一律 401 `UNAUTHENTICATED`。
- **跨租户矩阵**(`tests/test_*scope_api.py`、`test_document_grants_api.py`、`test_audit_api.py`):401;403 越界(收窄条件 / `tenant` 参数 / 非管理员);404 跨租户对象;空授权集合零命中;撤销后缓存立即失效;平台管理员逐租户访问并留痕。
- **门禁**:黄金数据集权限边界通过率 100%,检索指标不劣于基线(见 [0013 报告](docs/iterations/0013-evaluation-principal-scope.md))。
- **前端**:登录页与 401 回登录;授权清单只对文档所有者与租户管理员可写。

## 开发

```bash
uv run pytest                   # 全量测试,不触网
uv run ruff check .             # lint
uv run python scripts/evaluate.py         # 黄金数据集检索指标(默认无密钥)
uv run python scripts/evaluate.py --min-score 0.07 --check  # CI 同口径门禁
uv run python scripts/benchmark_ingestion.py # 入库形态上传阻塞时长(同步 vs 异步)
uv run python scripts/benchmark_qa_cache.py  # 同题二次询问延迟(缓存关 vs 开,T15)
uv run python scripts/benchmark_stats.py   # 统计口径与成本折算核对(T16)
uv run python scripts/check_upstream.py   # 真实上游连通性(需密钥,会计费)
```

### 本地登录与种子账号

服务启动**不会**建号。首次开发按「快速开始」拉起 PostgreSQL 并显式运行种子脚本;脚本幂等,重复执行只更新资料与口令哈希,口令只从 `SEED_<NAME>_PASSWORD` 读取(不写入仓库、不打印)。

```bash
SEED_MEMBER_PASSWORD='...' SEED_ADMIN_PASSWORD='...' \
  uv run python scripts/seed_principals.py --yes
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}'
```

测试不需要种子脚本:`tests/support.py` 用与配置同一密钥签发**真实** JWT,默认 `client` 即种子租户 `default` 的 `member`;匿名客户端仍能断言 401。内存模式不建号,只供测试夹具注入。

前端是**独立的工程**,有与后端对等的质量门槛(不塞进 `uv run pytest`):

```bash
cd frontend
pnpm typecheck    # vue-tsc,零错误
pnpm lint         # eslint,零 error
pnpm test         # vitest:API 客户端层 + 组件行为
pnpm build        # 生产构建
```

测试从预先约定的接缝进入:

- **HTTP API 层**:健康检查、错误映射、trace_id 一致性、堆栈不外泄;文档解析链路(五种格式、失败状态、落盘配置);文档清单(默认只列现行版本、筛选维度、分页与总数、排序口径);异步入库管线(上传立即返回、状态机进度、失败重试);向量化 / 检索;在线问答(有据回答 + 引用、低分拒答、查询日志),上游以确定性假向量与可注入生成器替身。
- **身份与跨租户矩阵**:真实 JWT 下的 401(缺失 / 过期 / 篡改)、403 越界(收窄条件或租户参数)、404 跨租户对象、空授权集合零命中、撤销后缓存立即失效、角色边界、查询日志 / 反馈所有权、统计收敛、平台管理员逐次审计;**OpenAPI 同步**声明 `HTTPBearer` 且登录端点保持公开。
- **评估主缝**:黄金数据集经同一组 HTTP 端点入库并检索,断言逐用例可追溯、权限边界与拒答用例生效、报告可重复渲染;无密钥、不触网。
- **统一 LLM 客户端的窄接缝**:假传输层注入超时 / 429 / 5xx,验证错误映射与用量解析。
- **前端 API 客户端层**:断言信封拆解取到数据、错误码映射到带 trace_id 的错误、CSV 端点返回原始文本、token 注入与 401 清理、请求参数与后端契约一致。
- **前端组件行为**:断言用户可见的行为(登录表单、未登录跳转、授权清单仅对所有者 / 管理员可见可操作、有据回答渲染引用、拒答时不渲染引用列表、反馈状态回显、状态标签呈现、错误详情展示可复制的 trace_id),不测组件内部状态变量。

## 目录结构

```
app/
  asgi.py                    # ASGI 入口(只装配,供 uvicorn)
  main.py                    # 应用工厂 create_app
  api/routes/                # HTTP 路由(健康检查 / 登录 / 文档 / 问答 / 统计 / 反馈 / 管理)
  core/                      # 配置 / 异常 / 信封 / 日志 / trace_id / 异常处理器
  domain/                    # 领域层:ParsedDocument schema / 状态机 / 身份 / 授权 / 仓储接口(权威契约)
  services/                  # 应用服务:入库编排 / 落盘存储 / 文档清单 / 鉴权与授权范围 / 审计
  repositories/              # 仓储实现(内存基线 / Chroma / PG 生产持久化)
  integrations/              # 实现层零件
    llm/                     #   统一 LLM 客户端与可注入传输层
    parsing/                 #   五种格式解析器与工厂(ADR-0002)
frontend/                    # 前端工作台(独立 Vue 3 工程,不进 Python 依赖体系)
  src/api/                   #   HTTP 收口层(全站唯一发请求处,统一注入 token)
  src/auth/                  #   凭证存取与会话失效通知
  src/views/                 #   登录页 + 六个业务页
  src/types/                 #   手写的响应类型(以契约为准)
  src/stores/                #   Pinia(当前主体等跨页面状态)
evaluation/                  # 黄金数据集(v1 YAML)与评估语料
tests/                       # 按接缝组织的测试(含跨租户 / 跨主体矩阵)
scripts/evaluate.py          # 检索指标评估器(默认无密钥)
scripts/seed_principals.py   # 显式创建种子账号(幂等,口令只从环境变量读取)
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
- [ADR-0015](docs/adr/0015-frontend-stack-and-boundary.md):前端栈与边界:独立 Vue 3 SPA 置于 `frontend/`,后端零改动、零 CORS
- [ADR-0019](docs/adr/0019-embedding-identity-and-dimension-guard.md):向量来源身份与维度守卫:不同身份不混算,换模型整库重嵌
- [ADR-0020](docs/adr/0020-identity-and-tenant-isolation.md):身份、租户硬隔离与必填授权范围:短期 JWT、平台管理员与审计
- [ADR-0021](docs/adr/0021-document-level-authorization.md):文档级授权:默认不可读、允许集合前过滤、授权随逻辑文档存活
- [T06 检索指标基线](docs/iterations/0001-t06-retrieval-baseline.md):24 条黄金用例,含逐用例追溯与后续对比约定
- [评估按主体与授权范围](docs/iterations/0013-evaluation-principal-scope.md):同一黄金数据集改走真实 JWT 与授权范围,权限边界仍 100%
- 身份、租户隔离与文档级授权的验收标准:[issue #25](https://github.com/LjjCode536/rag-knowledge-assistant/issues/25)
- 里程碑与验收标准:[issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)
