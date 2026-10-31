# rag-knowledge-assistant

企业 AI 知识库:生产级 RAG(Retrieval-Augmented Generation)服务的迭代式实践。

后端 FastAPI + 自建领域层(schema / 服务分层 / 状态机),框架层使用 LangChain 成熟组件作零件库;模型服务统一走硅基流动(OpenAI 兼容)。前端是独立的 Vue 3 SPA,置于 `frontend/` 子目录,不进入 Python 依赖体系。开发遵循"评估驱动迭代":每次迭代产出可复现的前后指标对比。内部教材位于 `local doc/`(已被 gitignore,永不入库)。

## Commands

```bash
uv sync                                        # 安装依赖(含 dev)
uv run pytest                                  # 全量测试(不读 .env;不触外网,本地 PG 不可达时 pgvector 集成用例自动跳过)
uv run ruff check .                            # lint
uv run python -m uvicorn app.asgi:app --reload # 本地启动
uv run python scripts/evaluate.py              # 黄金数据集检索指标(默认无密钥)
```

真实上游连通性检查需密钥且会计费:`uv run python scripts/check_upstream.py`。真实上游评估用
`uv run python scripts/evaluate.py --embedder upstream`(或 `--base-url`),报告中会注明 embedder。

### 前端(独立工程,两套依赖互不污染)

前端工程在 `frontend/` 子目录,包管理器用 pnpm。**前端命令不进 `uv run pytest`**:
后端测试不跑前端测试,前端测试也不碰 Python 环境。

```bash
cd frontend
pnpm install        # 安装依赖
pnpm dev            # 开发服务器(http://localhost:5173;proxy 把 /api 与 /health 转发到后端)
pnpm build          # 生产构建
pnpm typecheck      # vue-tsc,零错误
pnpm lint           # eslint,零 error
pnpm test           # vitest:API 客户端层 + 组件行为
```

本地开发要同时起后端与前端 dev server。后端**零改动、零 CORS** —— 跨源由 Vite proxy
解决(ADR-0015)。

## 前端纪律

### 1. 前端不得直接发 HTTP 请求

所有 HTTP 调用经 `api/` 层**唯一的底层请求函数**(`frontend/src/api/client.ts` 的
`request` / `requestRaw`),职责固定为四步:发请求 → 拆响应信封 → 按错误码抛类型化
错误 → 带上 `trace_id`。**页面组件只调 `api/` 层的函数**;组件里出现 `fetch` /
`axios` 即为违规。

### 2. 前端术语纪律

界面文案与代码标识符遵循根目录 `GLOSSARY.md` 的规范术语:知识库文档 / 切片 / 引用 /
查询日志 / 拒答 / 入库进度。**不使用**其 `_Avoid_` 清单里的同义词:文件、片段、
source、出处、分段、评价、点赞。

### 3. 前端不制造不存在的安全边界

后端当前**没有用户、没有会话、没有鉴权**,任何调用方都能传任意 `tenant`。界面上的
租户切换**不是**权限控制,只是一个筛选维度;前端如实呈现这一现状,不做假登录。

### 4. 前端类型以契约为准且手写

契约由后端 OpenAPI(`/openapi.json`)定义,前端类型**手写**并以其为准,**不自动生成**
—— 保持前端类型是契约的一个显式声明,避免生成物掩盖契约变化。

### 5. 前端测试只测外部行为

组件测试断言用户可见的行为(渲染出了什么、点击触发了什么),**不测**组件内部状态
变量、不测 Element Plus 自身行为。替身:组件测试不真打后端,用 `fetch` 拦截层注入
固定的信封响应 —— 与后端"以假 LLM / 假向量库替身替换真实上游"是同一思路。

## Agent skills

### Issue tracker

issue 与 spec 都放在 GitHub Issues(`LjjCode536/rag-knowledge-assistant`),全部通过 `gh` CLI 操作。See `docs/agents/issue-tracker.md`.

### Triage labels

使用五个规范角色标签的默认词汇:`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

单上下文布局:根目录 `GLOSSARY.md` + `docs/adr/`。See `docs/agents/domain.md`.
