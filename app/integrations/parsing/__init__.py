"""文档解析实现层(ADR-0001 的"零件位")。

解析库选型见 ADR-0002:pymupdf / python-docx / beautifulsoup4,
与教材第 40 章生产替换清单同款;LangChain loader 可作为替换单个解析器的备选。
领域层只认 ParsedDocument / DocumentElement,框架类型止步于此。
"""
