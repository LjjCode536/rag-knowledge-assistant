# ADR-0017: Markdown 解析改用 markdown-it-py,HTML 噪声边界收口到页面级

**Status**: accepted

## Context

`app/integrations/parsing/markdown_parser.py` 原先用手写正则逐行扫描,在真实写法上已经在丢信息:

- 列表标记被丢弃(`_LIST_ITEM` 只取正文),`1. 起赔金额` 入库后变成 `起赔金额`,条款序号与编号列表消失,正好踩中《RAG 开发规范》5.5 的"禁删项";
- 嵌套列表被拍平成同级元素,层级信息丢失;
- 表格要求行首行尾都有 `|`,GFM 允许的无首尾竖线表格识别不了;
- 不支持 Setext 标题、缩进代码块、HTML 块。

《RAG 开发规范》4.4 直接建议用 `markdown-it-py` / `mistune` 解析 AST 替代手写行扫描。这与 ADR-0002 并不冲突:ADR-0002 拒绝的是 langchain-community loader 与 Unstructured,不是轻量解析库本身。`markdown-it-py` 的 token 带 `map`,能把项目现有的行号定位元数据保住。

HTML 侧现状走的是《RAG 开发规范》4.5 允许的"BeautifulSoup 手动白名单"回退路径,但噪声标签名单只覆盖 `script/style/head` 一类,`nav / aside / iframe / form` 等页面装饰会直接进正文;`header / footer` 则存在歧义:页面级的是导航噪声,`<article><header>` 里往往是标题与作者,全量剔除会误删正文。

## Decision

- **Markdown 引擎换成 `markdown-it-py`**:`MarkdownIt("commonmark").enable("table")`(commonmark 预设不含 GFM 表格),按 token 类型映射为解析元素:
  - 标题(ATX / Setext)→ `TITLE`,层级取 `h1`–`h6`;
  - 段落 / 引用块 → `PARAGRAPH`,引用符号 `>` 由 token 边界消化,不进正文;
  - 每个 `list_item` → 一个 `LIST` 元素,content 保留标记(`- ` / 按 `start` 重算的 `N. `);嵌套项按文档序展开为独立元素,不建模层级;
  - 表格 → 单个 `TABLE` 元素,单元格以 ` | ` 连接,兼容无首尾竖线写法;
  - 围栏 / 缩进代码块 → `CODE`,原样保留缩进;项内代码块也单独成元素;
  - HTML 块 → 原样作为 `PARAGRAPH`,不递归解析;
  - 行内 Markdown 标记保持原样,链接 URL 因此保留在正文里;
  - 行号统一取 `token.map` 并转 1 基。
- **HTML 噪声边界**:无条件跳过新增 `nav / aside / form / iframe / svg / canvas / button / select / input / textarea`;`header / footer` 仅在直接挂在 `<body>` 下时剔除,嵌套在 `article / section / div` 里的保留。
- **不做文档标题与链接元数据**:`ParsedDocument` 目前没有 title 字段,`Chunk.title` 的语义是节标题(引用文案 `标题: ...` 用它);知识库文档也没有"原始 URL"概念,记 href 没有消费者。等引用侧真正需要"文档标题"时,以 `document_title` 单独命名再加,不复用 `Chunk.title`。
- **不引入 trafilatura**:它是面向网页文章的正文抽取器,对内网文档、Confluence 导出、表格密集页面容易抽过头,且抽完仍需 BS4 二次提取结构。先靠噪声名单收敛;只有当 fixture 数据显示导航垃圾仍显著时才另开 issue 评估。
- **存量文档不重新解析**:改动只影响新上传;需要刷新时按同名重传走版本退位即可。

## Consequences

- **新增依赖**:`markdown-it-py`(纯 Python,无原生扩展)。
- **行为变化(有意)**:`LIST` 元素的 content 现在包含列表标记,`tests/test_documents_api.py` 的断言随语义更新;无首尾竖线表格、Setext 标题、嵌套列表、HTML 块由此前"识别不了/丢信息"变为可解析。
- **契约不变**:`ParsedDocument` / `DocumentElement` / 工厂 / 分派链路与切分器都不动,新增的是解析器内部实现与元素内容口径。
- **评估基线不变**:黄金语料六篇 md 不含列表与表格,`scripts/evaluate.py` 跑出 Recall@3 0.7917 / MRR 0.7500 / NDCG@3 0.7609,与改动前一致;解析改动的回归由新增的 `tests/test_document_parsing.py` 承担。
- **不回溯**:升级解析器不会自动重算已入库文档的 `parsed` 与切片;旧结果保持原样。
- **后续触发条件**:若引入"文档标题"或"原始链接",先补消费者(引用展示/过滤),再改契约并登记术语;若引入 trafilatura,须以 fixture 对比元素数、正文覆盖率与表格保留率。
