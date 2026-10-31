# 迭代 0009:T17 后端:知识库文档列表端点

**工单**:issue #19(T17,父 #18) · **ADR**:ADR-0015(前端栈与边界)

## 背景与可控指标

后端此前能上传、能按 id 查单份文档,但**列不出自己有哪些文档**:
`DocumentRepository` 契约只有登记 / 按 id 取 / 保存 / 按标识取现行版本四件事,
没有列出与计数;路由侧没有文档清单端点;运营统计只给聚合数字
(`document_count` / `indexed_document_count` / `chunk_count`),不给明细。

这不是"前端的需求膨胀",是**后端能力的真实缺口**:一个知识库服务不能列出
自己的文档。ADR-0015 把"前端不改后端"原为规避跨源与流式两件真麻烦而设,
并把补这条只读清单端点列为该边界的**显式例外** —— `/stats` 已在做
`count(*)`,`list` 是同一张表同一套过滤条件的自然延伸。

本迭代的可控指标:

1. **列举口径**:默认只列现行版本(退位旧版本与已删除文档不进清单),想看
   历史时给参数能看;
2. **筛选维度**:租户 / 状态 / 关键字 / 部门 / 文档类型五维可用,多个条件
   按"与"组合;
3. **分页与总数**:分页正确,`total` 是符合筛选条件的总数(不受分页影响),
   越界不报错;
4. **两种实现同口径**:内存实现与 PostgreSQL 实现在同一组参数下返回
   相同的文档集合、相同顺序、相同总数。

## 做法

- **口径收口在领域层**:`DocumentFilter`(app/domain/documents.py)表达筛选
  与分页条件(租户 / 状态 / 关键字 / 部门 / 文档类型 / `latest_only` /
  `limit` / `offset`)。路由层把查询参数装配成它,仓储实现只认它 ——
  内存实现与 PostgreSQL 实现因此共享同一套口径(ADR-0001:换实现只换实现)。
- **契约加 `list` / `count`**:与既有 `add` / `get` / `save` /
  `find_latest_by_file` 并列。两者用**同一份筛选条件**,故 `total` 与清单
  口径必然一致。
- **默认只列现行版本**:内存实现按 `is_latest` 过滤,PostgreSQL 侧同样
  (`WHERE is_latest`);`include_history=true` 时两者都放开。
- **排序口径固定**:创建时间倒序(最新在前);创建时间相同时按 `id` 兜底 ——
  次序确定,不依赖存储实现的偶然顺序(内存实现按插入序,PostgreSQL 侧按
  `seq` 插入序,两者都不作为业务次序暴露)。
- **关键字大小写不敏感**:内存实现用 `file_name.lower()` 子串匹配;
  PostgreSQL 侧用 `ILIKE`,并**转义通配符**(`%` / `_` / `\`)—— 用户输入的
  `%` 按字面量匹配,不当通配符。
- **空值归一在服务层**:`DocumentCatalogService`(app/services/document_catalog.py)
  把空白串按"未提供"处理,并用同一份条件分别取清单与总数。
- **只读端点**:`GET /api/v1/documents` 返回统一响应信封,`data` 为
  `{total, limit, offset, items[]}`;条目与单份查询的档案视图同形
  (`DocumentView.of`),故前端两种用法拿到同一套字段。

## 验收(issue #19 逐条)

- [x] 领域层文档仓储契约提供列出与计数能力,入参能表达筛选条件与分页,
      并能返回符合条件的总数 —— `DocumentFilter` + `DocumentRepository.list` /
      `count`;`tests/test_documents_list_pg.py::test_pg_list_matches_memory_for_every_filter`
- [x] 进程内存实现与 PostgreSQL 实现同契约:同一组筛选与分页参数下,两种
      实现返回相同的文档集合、相同顺序、相同总数 ——
      `tests/test_documents_list_pg.py::test_pg_list_matches_memory_for_every_filter`
      (15 组参数逐一比对 id 序列与计数)
- [x] 默认只列现行版本:已退位的旧版本与已删除的文档不出现在默认清单里;
      提供参数后可以查到它们 ——
      `tests/test_documents_list_api.py::test_list_defaults_to_latest_versions_only`、
      `test_list_excludes_deleted_documents_by_default`、
      `tests/test_documents_list_pg.py::test_pg_list_default_hides_superseded_and_deleted`
- [x] 默认按创建时间倒序;创建时间相同时有确定性的次序,不依赖存储实现的
      偶然顺序 —— `tests/test_documents_list_api.py::test_list_sorted_by_created_at_descending`、
      `tests/test_documents_list_pg.py::test_pg_list_sorted_by_created_at_descending_with_deterministic_tiebreak`
- [x] 路由层暴露文档清单端点,响应走统一响应信封,筛选与分页参数与契约
      一致,总数随响应返回 —— `tests/test_documents_list_api.py::test_list_response_uses_envelope_with_trace_id`
- [x] 关键字筛选命中文件名的子串,大小写不敏感;租户与状态筛选精确匹配;
      多个筛选条件同时给出时按"与"组合 ——
      `tests/test_documents_list_api.py::test_list_filters_by_keyword_case_insensitive_substring`、
      `test_list_filters_by_tenant_exactly`、`test_list_filters_by_status_exactly`、
      `test_list_combines_filters_with_and`
- [x] 分页越界(偏移超出总数)返回空清单与正确总数,不报错 ——
      `tests/test_documents_list_api.py::test_list_offset_beyond_total_returns_empty_with_correct_total`、
      `tests/test_documents_list_pg.py::test_pg_list_offset_beyond_total_returns_empty_with_correct_total`
- [x] 内存实现的契约测试全绿(API 层驱动,断言外部行为) ——
      `tests/test_documents_list_api.py` 13 例
- [x] PostgreSQL 真库的同口径测试全绿;**数据库不可达时该批测试自动跳过**,
      不因无 Docker 而失败 —— `tests/test_documents_list_pg.py` 6 例
      (`test_pg_database` 夹具负责跳过)
- [x] 既有测试不受影响(不改动其他端点的契约与行为) —— 全量
      `uv run pytest` **404 passed / 0 skipped**(基线 385 passed)

### 真库验证与变异验证

真库(本地 Docker 的 PostgreSQL 16.15 + pgvector 0.8.6)上跑通
`tests/test_documents_list_pg.py` 6 例,并做了三处**变异验证**确认断言
真的有效(不是恒过的摆设):

- 内存实现忽略 `latest_only`(`if False:`)→
  `test_list_defaults_to_latest_versions_only`、
  `test_list_excludes_deleted_documents_by_default`、
  `test_pg_list_matches_memory_for_every_filter` 三例失败;
- PostgreSQL 实现忽略 `latest_only`(去掉 `is_latest` 条件)→
  `test_pg_list_matches_memory_for_every_filter`、
  `test_pg_list_default_hides_superseded_and_deleted`、
  `test_pg_list_offset_beyond_total_returns_empty_with_correct_total` 三例失败;
- PostgreSQL 关键字匹配改大小写敏感(`ILIKE` → `LIKE`)→
  `test_pg_list_matches_memory_for_every_filter` 失败(大小写不敏感的
  关键字漏命中)。

## 口径(ADR-0015)

- **默认只列现行版本**:`is_latest = true`。退位的旧版本与已删除的文档
  不进默认清单;`include_history=true` 可查历史(退位与删除是**终态**,
  档案保留可查 —— 清单的默认口径不是"删掉记录",只是不默认展示)。
- **排序**:创建时间倒序;同刻按 id 兜底。
- **筛选**:租户 / 状态 / 部门 / 文档类型精确匹配;关键字命中文件名子串,
  大小写不敏感;多条件按"与"组合。
- **分页**:`limit`(1–200,缺省 50)/ `offset`;`total` 是符合筛选条件的总数,
  不受分页影响;越界返回空清单 + 正确总数,不报错。
- **租户是筛选维度,不是权限边界**:后端当前没有用户 / 会话 / 鉴权,任何
  调用方都能传任意 `tenant`;这条清单端点如实呈现该现状(ADR-0015)。

## 已知边界与后续

- **清单不含解析产物**:条目是档案视图(状态 / 元数据 / 计数 / 时间戳),
  `parsed` 全文不进清单(体积原因);解析元素另走
  `GET /api/v1/documents/{id}/elements`。
- **租户集合尚未单独成端点**:前端全局租户选择器的数据源(issue #20 里
  "数据源后接")可以取清单后在前端聚合,也可另开一条只读端点;本次不做,
  避免在无消费方时先造端点。
- **无服务端排序参数**:排序固定为创建时间倒序(issue #19 的验收口径),
  不开放"按文件名 / 按切片数排序"—— 前端若需要,另开迭代。
- **T18 前端工程**是本次的前置依赖的消费者:文档列表页直接消费本端点的
  `total` / `limit` / `offset` / `items` 四字段。
