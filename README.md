# 企业 AI 知识库(生产级 RAG)

生产级企业知识库服务的迭代式实践:后端 FastAPI + 自建领域层,框架层以 LangChain 成熟组件当零件库,模型服务统一走硅基流动(OpenAI 兼容)。开发遵循"评估驱动迭代",每个里程碑都产出可复现的前后指标对比。

> 当前进度:**M3 向量化与最小问答链路** + **T06 评估基座**(批量向量化 / 内存向量库 / 检索前过滤 / 有据回答 + 引用 / 低分拒答 / 查询日志 / 黄金数据集 + 检索指标);M0–M2 已就位。路线图见 [issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)。

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
  "data": {"status": "ok", "version": "0.1.0", "environment": "local", "upstream_configured": true},
  "details": null,
  "trace_id": "demo-trace-0001",
  "timestamp": "2026-10-28T09:00:00Z"
}
```

响应头 `X-Request-ID`、响应体 `trace_id` 与全部日志共享同一个值;调用方传入则透传,缺失或格式非法时服务生成。

### 文档入库(M1)

上传即同步解析,支持 txt / md / html / docx / pdf:

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
    "status": "parsed",
    "element_count": 12,
    "page_count": 3
  }
}
```

- `GET /api/v1/documents/{id}`:状态机位置全程可查(`uploaded → parsing → parsed`,任一步失败进入 `failed` 并带 `error_message`)。
- `GET /api/v1/documents/{id}/elements`:解析产物,元素带类型与页码 / 行号 / 标题层级等定位元数据。
- 解析失败不静默吞错:档案照常建立,`status=failed`、原因可查;不认识的扩展名返回 `415 UNSUPPORTED_FORMAT`,不生成档案。
- 落盘位置由 `UPLOAD_DIR` 配置(默认 `data/uploaded_docs`),运行时产物不入库。

### 向量化与检索(M3)

切片之后批量向量化并写入内存向量库;检索支持 top-k 与**检索前**元数据过滤:

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
  持久后端(教材第 42 章):同题同结果,切换前后指标对比见
  `docs/iterations/0004-t12-vector-store-switch.md`。
- 向量库的 `VectorStore` 契约在领域层;换后端只换实现,后续 pgvector 沿同一路径。
- 过滤发生在相似度计算之前:被权限条件排除的切片不占 top-k 名额。
- 未配置密钥时返回统一上游错误,文档停在 `chunked` 可直接重试,不落 `failed`。

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
    "latency_ms": 4.21
  }
}
```

- 资料不足时统一拒答(`refused=true`、`refusal_reason` 为 `no_hits` / `low_score` / `generator_refused`),答案固定为 `根据当前资料无法确认。`,且不返回引用。
- 拒答阈值由 `QA_MIN_SCORE` 配置(默认 `0.35`);默认 `QA_TOP_K=3`。
- 查询日志:`GET /api/v1/query-logs?limit=20`(最近优先)与 `GET /api/v1/query-logs/{query_id}`,含原始问题与改写 / 扩展后的问题、命中 / 分数、是否进入证据、拒答原因、生成器种类(`generator`=`rule_based` / `llm`)、Token 用量(`token_usage`)与延迟。
- 回答生成器默认真实 LLM 有据生成(`QA_GENERATOR=llm`,默认模型 `Qwen/Qwen3-8B`,经 `LLM_MODEL` 可换):

  - Prompt 强约束:只依据编号资料回答、不足即统一拒答、绝不编造,回答用 `[编号]` 标注所依据的资料;
  - 上下文为空直接拒答,不触上游;上游超时 / 限流等错误经统一客户端转为 `UPSTREAM_TIMEOUT` / `UPSTREAM_RATE_LIMITED` 等稳定错误码;
  - `QA_GENERATOR=rule` 可切回规则版基线(不触网、零 Token),两种模式在 `generator` 字段上可观测;
  - 真实上游验证:`uv run python scripts/check_upstream.py`(需密钥、会计费)。

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
| `BAD_REQUEST` | 400 | 请求参数不合法 |
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
uv run python scripts/check_upstream.py   # 真实上游连通性(需密钥,会计费)
```

测试从预先约定的接缝进入:

- **HTTP API 层**:健康检查、错误映射、trace_id 一致性、堆栈不外泄;文档解析链路(五种格式、失败状态、落盘配置);向量化 / 检索;在线问答(有据回答 + 引用、低分拒答、查询日志),上游以确定性假向量与可注入生成器替身。
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
  repositories/              # 仓储实现(进程内存,M3 换 PostgreSQL)
  integrations/              # 实现层零件
    llm/                     #   统一 LLM 客户端与可注入传输层
    parsing/                 #   五种格式解析器与工厂(ADR-0002)
evaluation/                  # 黄金数据集(v1 YAML)与评估语料
tests/                       # 按接缝组织的测试
scripts/evaluate.py          # 检索指标评估器(默认无密钥)
scripts/check_upstream.py    # 需要密钥的连通性脚本
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
- [T06 检索指标基线](docs/iterations/0001-t06-retrieval-baseline.md):24 条黄金用例,含逐用例追溯与后续对比约定
- 里程碑与验收标准:[issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)
