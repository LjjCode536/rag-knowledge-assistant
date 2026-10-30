# rag-knowledge-assistant

企业 AI 知识库:生产级 RAG(Retrieval-Augmented Generation)服务的迭代式实践。

后端 FastAPI + 自建领域层(schema / 服务分层 / 状态机),框架层使用 LangChain 成熟组件作零件库;模型服务统一走硅基流动(OpenAI 兼容)。开发遵循"评估驱动迭代":每次迭代产出可复现的前后指标对比。内部教材位于 `local doc/`(已被 gitignore,永不入库)。

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

## Agent skills

### Issue tracker

issue 与 spec 都放在 GitHub Issues(`LjjCode536/rag-knowledge-assistant`),全部通过 `gh` CLI 操作。See `docs/agents/issue-tracker.md`.

### Triage labels

使用五个规范角色标签的默认词汇:`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

单上下文布局:根目录 `GLOSSARY.md` + `docs/adr/`。See `docs/agents/domain.md`.
