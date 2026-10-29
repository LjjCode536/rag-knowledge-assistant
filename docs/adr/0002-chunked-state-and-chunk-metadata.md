# ADR-0002: 状态机新增 chunked 落点;切片元数据在上传入口登记

**Status**: accepted

## Context

M0 状态机"先固定契约"为 `parsed → chunking → embedding`,本意是 M2 / M3 各接入一段。真实实现 M2(切片)时出现了两个契约没覆盖的空当:

1. **切片完成的落点**:向量化(M3)尚未存在,`chunking` 完成后文档没有合法去处——迁往 `embedding` 等于谎报进度,停在 `chunking` 又等于谎报"正在切片"。
2. **切片元数据的来源**:切片必须携带租户、部门、文档类型、版本,而 M1 的 `DocumentRecord` 与上传端点都没有这些字段。

## Decision

- 状态机在 M2 做一次**增量细化**(不推翻,只追加):新增落点状态 `chunked`,合法迁移动态为 `parsed → chunking → chunked`、`chunked → chunking`(重切换策略 / 参数)。M3 接入向量化时只需追加 `chunked → embedding` 一行,原有骨架不动。
- 切片元数据在**上传入口登记**:上传端点增加表单字段 `tenant / department / doc_type / version`(可省,缺省 `tenant=default`、`version=v1`,部门与文档类型缺省为未填写),存入文档档案;切片时冗余到每个切片。这样"同档案多轮重切"不用反复传元数据,也更贴近 GLOSSARY 中"知识库文档本身具有租户、部门、文档类型、版本"的定义。

## Consequences

- 状态机注释里"M2 / M3 先固定契约"的说法由本 ADR 取代;`chunked` 是规范落点,测试与文档一律使用它。
- 单一切片请求始终是文档当前切片的**整组替换**:旧切片作废、重新编号,不会出现同一文档两代切片并存;M3 的向量化落库要以此为前提。
