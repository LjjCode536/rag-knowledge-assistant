# ADR-0002: 文档解析直接用轻量解析库,不引入 langchain-community loader

**Status**: accepted

## Context

M1 要把 txt / md / html / docx / pdf 解析成带定位元数据(页码 / 行号 / 标题层级)的 ParsedDocument。候选路径:langchain-community 的 Document Loader 家族、Unstructured 全家桶、或教材第 40 章"解析库选型"给出的轻量组合(`pymupdf / python-docx / beautifulsoup4 + lxml`)。issue #3 明确允许"文档同款开源解析库或 LangChain loader"二选一。

## Decision

采用教材同款轻量解析库,自写 Parser、工厂分发;LangChain loader 作为单个解析器的备选实现预留(解析器接缝不变)。理由:

- **需要元素级结构**:ParsedElement 要带标题层级、行号、页码;LangChain loader 只产出扁平的 `page_content + metadata`,元素结构仍要自己拆,等于绕一圈回到原点
- **依赖负担**:langchain-community 连带依赖面大;Unstructured 体积更大,在 Python 3.13 / Windows 上明显脆弱
- **本质相同**:pymupdf / python-docx / beautifulsoup4 正是相应 loader 底层使用的引擎,直接用框架类型零泄漏,与 ADR-0001 的边界要求一致
- **pymupdf 钉在 1.x(`<2`)**:2.0 起引入 pymupdf-layout / onnxruntime / numpy 依赖链(为版面理解模型服务),文本提取用不上;教材对应的 1.x 是零依赖单 wheel,符合"轻量零件库"定位

## Consequences

- 五个解析器全部位于 `app/integrations/parsing/`,领域层只认 `ParsedDocument / DocumentElement`
- PDF 无文本层(扫描件)显式报错并提示 need_ocr,M1 不做 OCR(项目 out of scope)
- 后续若某格式需要更强的版面理解(如复杂 PDF),只替换工厂里的单个解析器,契约不动
