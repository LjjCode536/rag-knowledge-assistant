# ADR-0001: 自建领域层,LangChain 组件当零件库

**Status**: accepted

## Context

项目要做成生产级企业知识库,同时遵循两条原则:能用开源成熟方案就不造轮子;保留教材中 schema 式的工程化结构(ParsedDocument / 切片 / 服务分层 / 状态机)。框架选型存在真实分歧:全套框架、RAG 专用框架、还是只用基础库。

## Decision

自建领域层(schema、服务分层、状态机、仓储接口)作为唯一权威契约;LangChain 只以组件形式进入实现层(解析 loader、text-splitters、检索器、向量库集成),不当骨架、不向业务代码泄漏框架类型。教材第 43 章的"生产替换清单"本身就是 LangChain 组件,代码与教材一一对应。

## Considered Options

- **LangChain 组件当零件库(选定)**:与教材对应度最高,换件成本最低
- **LlamaIndex 主干**:IngestionPipeline 生产能力白送,但组件名与教材不对应,且与自建统一 LLM 客户端职责重叠
- **LangChain + LangGraph 全套**:编排能力最强,但 M0–M5 复杂度陡增,留给 M7 之后的独立迭代再评估

## Consequences

- 硅基流动 rerank 接口(Jina/Cohere 兼容)需要自写一个薄适配器(约 50 行),这是全项目唯一允许的"小轮子"
- 领域 schema 是权威:框架类型的转换只发生在适配层边界
- 后续若整体迁移到 LlamaIndex,只替换实现层,领域层与 API 契约不动
