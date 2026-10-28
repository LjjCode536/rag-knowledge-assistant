# rag-knowledge-assistant

企业 AI 知识库:生产级 RAG(Retrieval-Augmented Generation)服务的迭代式实践。

后端 FastAPI + 自建领域层(schema / 服务分层 / 状态机),框架层使用 LangChain 成熟组件作零件库;模型服务统一走硅基流动(OpenAI 兼容)。开发遵循"评估驱动迭代":每次迭代产出可复现的前后指标对比。内部教材位于 `local doc/`(已被 gitignore,永不入库)。

## Agent skills

### Issue tracker

issue 与 spec 都放在 GitHub Issues(`LjjCode536/rag-knowledge-assistant`),全部通过 `gh` CLI 操作。See `docs/agents/issue-tracker.md`.

### Triage labels

使用五个规范角色标签的默认词汇:`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。See `docs/agents/triage-labels.md`.

### Domain docs

单上下文布局:根目录 `GLOSSARY.md` + `docs/adr/`。See `docs/agents/domain.md`.
