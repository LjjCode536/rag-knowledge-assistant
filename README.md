# 企业 AI 知识库(生产级 RAG)

生产级企业知识库服务的迭代式实践:后端 FastAPI + 自建领域层,框架层以 LangChain 成熟组件当零件库,模型服务统一走硅基流动(OpenAI 兼容)。开发遵循"评估驱动迭代",每个里程碑都产出可复现的前后指标对比。

> 当前进度:**M0 工程骨架**(配置 / 异常 / 日志 trace_id / 统一 LLM 客户端 / 响应信封 / 健康检查 / 测试与 lint 基线)。路线图见 [issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)。

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
uv run python scripts/check_upstream.py   # 真实上游连通性(需密钥,会计费)
```

测试从两个预先约定的接缝进入:

- **HTTP API 层**:健康检查、错误映射、trace_id 一致性、堆栈不外泄。
- **统一 LLM 客户端的窄接缝**:假传输层注入超时 / 429 / 5xx,验证错误映射与用量解析。

## 目录结构

```
app/
  asgi.py                    # ASGI 入口(只装配,供 uvicorn)
  main.py                    # 应用工厂 create_app
  api/routes/                # HTTP 路由
  core/                      # 配置 / 异常 / 信封 / 日志 / trace_id / 异常处理器
  integrations/llm/          # 统一 LLM 客户端与可注入传输层
tests/                       # 按接缝组织的测试
scripts/check_upstream.py    # 需要密钥的连通性脚本
docs/adr/                    # 架构决策记录
GLOSSARY.md                  # 领域术语表(唯一权威词汇)
```

## 文档

- [GLOSSARY.md](GLOSSARY.md):领域术语
- [ADR-0001](docs/adr/0001-langchain-parts-domain-layer.md):自建领域层,LangChain 组件当零件库
- 里程碑与验收标准:[issue #1](https://github.com/LjjCode536/rag-knowledge-assistant/issues/1)
