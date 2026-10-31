# ADR-0015: 前端栈与边界——独立 Vue 3 SPA,置于 frontend/ 目录,与后端同源代理

**Status**: accepted

## Context

本仓库此前只有后端(FastAPI + 自建领域层),对外形态是纯 JSON API:9 个路由模块
(健康检查 / 文档 / 切片 / 向量化 / 检索 / 问答 / 查询日志 / 反馈 / 统计),无 CORS
中间件、无静态文件挂载、无模板引擎,`/chat` 无流式输出。补前端时,三个问题需要
回答:选什么栈、前端放哪、要不要动后端。

事实基础(已核实):

- 后端**没有 CORS**——跨源浏览器请求会被拦(`OPTIONS /api/v1/chat` 返回 405),
  跨源部署必然要求后端加 `CORSMiddleware`;
- 后端**无流式**——`/chat` 单次返回缓冲 JSON,想要打字机效果须改后端;
- 上传默认**异步**——`POST /api/v1/documents` 返回的是**任务句柄**(status 恒为
  `uploaded`),入库进度须轮询 `GET /api/v1/documents/{id}` 查状态机;
- 切片 / 切片失败**不返回错误码**——切片运行时失败返回 HTTP 200 但
  `document.status == "failed"`,只看 HTTP 状态码会漏判;
- `GET /api/v1/feedback/negative/candidates?format=csv` **破坏响应信封**,返回
  带 BOM 的裸 CSV。

## Decision

- **前端是独立的 Vue 3 SPA,置于 `frontend/` 子目录**,自带 `package.json` 与
  工具链,**不进入 Python 依赖体系**(`uv` / `pyproject.toml` 不感知前端)。
  选型:Vue 3.5 + Vite 8 + TypeScript + Element Plus 2.14 + vue-router +
  Pinia + TanStack Query(vue-query)。
- **前端是后端能力的真实视图,不做登录与鉴权**:租户只是一个筛选维度(下拉 /
  输入),前端不假装有身份与权限边界。后端当前**没有用户、没有会话、没有鉴权**,
  任何调用方都能传任意 `tenant`——前端如实呈现这一现状,不做假登录制造
  "已隔离"的错觉。真正的鉴权是后端的独立里程碑,不在"补前端"范围内。
- **开发期用 Vite dev server 的 proxy 打通后端**:`/api` 与 `/health` 代理到
  `127.0.0.1:8000`,**后端零改动、零 CORS**。生产期用同一域名反代同时服务静态
  产物与 API(nginx / Caddy),后端仍不需要 CORS。
- **前端不挂进 FastAPI**:不 `StaticFiles`、不 `Jinja2Templates`、不把构建产物
  放进 Python 包。后端保持"只提供 JSON API"的单一职责,构建产物缓存策略、
  静态资源版本与 API 版本解耦。
- **状态分层**:服务端状态(查询、统计、文档档案、轮询)归 TanStack Query;跨页
  面的客户端状态(当前租户筛选、主题)归 Pinia;组件局部状态用 `ref` /
  `reactive`。入库进度用 TanStack Query 的轮询能力表达,不手写
  `setInterval` + 生命周期清理。
- **第一版不引入流式**:问答采用"请求完成后前端打字机动画"的假流式,后端
  `/chat` 不动。真流式(SSE)作为独立的后端改动另开迭代——届时按 ADR-0012 的
  惯例单独记录决策。
- **前端遵循项目既有词汇**(`GLOSSARY.md` 为唯一权威):数据结构字段名跟随后端
  契约;界面文案用规范术语(知识库文档 / 切片 / 引用 / 查询日志 / 拒答 / 入库
  进度),不用 `_Avoid_` 清单里的同义词(文件 / 片段 / source / 出处)。
- **前端测试用 Vitest + @vue/test-utils,测组件行为与 API 客户端行为**,不测
  实现细节;接缝约定与后端一致("测试从预先约定的接缝进入")。端到端(Playwright)
  待组件行为稳定后另开迭代。

## Consequences

- **`frontend/` 与后端是两个工程**:本地开发要同时起后端(`uvicorn`)与前端
  (`vite`),Vite proxy 负责跨域;CI 需要两个独立的 lint / typecheck / test 环节。
- **后端 API 契约成为前端的强依赖**:改响应字段要先改前端类型。契约由后端
  OpenAPI(`/openapi.json`)定义,前端类型**手写**并以契约为准,不自动生成——
  保持前端类型是"契约的一个显式声明",避免生成物掩盖契约变化。
- **假流式不改善真实延迟**:界面上的打字机效果总时长与后端真实耗时相同,只是
  观感。首字节时间(TTFB)仍是后端全量响应时间,真流式才能改善它。
- **入库进度依赖轮询,不是推送**:进度查询的实时性受轮询间隔限制;`embedding`
  状态是请求内瞬时态(ADR-0012),轮询大概率观察不到。
- **前端不制造不存在的安全边界**:界面上的租户切换**不是**权限控制。真实隔离
  仍依赖后端在检索前过滤(ADR-0011),而当前后端接受调用方传入的任意租户。
- **CSV 导出破坏信封**:前端对该端点要特殊处理(不能走统一的信封拆解),
  且需要处理 UTF-8 BOM。
- **`failed` 状态不靠 HTTP 码识别**:入库失败在响应体里(`document.status ==
  "failed"` + `error_message`),前端必须检查文档状态而非仅看响应码。
- **后端缺"列出文档"的读端点,需补齐**:知识库文档列表页需要文档明细,
  但 `DocumentRepository` 契约只有 `add` / `get` / `save` / `find_latest_by_file`,
  路由侧没有 `GET /api/v1/documents` 列表端点——`/stats` 只给聚合数字
  (`document_count` / `indexed_document_count` / `chunk_count`),不给明细。
  前端因此**无法从后端取回文档清单**,「知识库文档」页若无列表端点将退化为
  "上传 + 轮询单文档",或被迫在前端本地(localStorage)另存一份文档列表——
  后者在生产持久化(pgvector)模式下会制造"前端所知的文档"与"后端所知的文档"
  两套真相,与 ADR-0009 的"清理先行、状态机后置"取向冲突(退位与删除必须
  以服务端状态为准)。故本次补一条**只读**列表端点:
  - 领域层 `DocumentRepository` 契约加 `list` / `count`(与 `add` / `get` /
    `save` / `find_latest_by_file` 并列),内存实现与 PostgreSQL 实现同切
    (ADR-0001:换实现只换实现);
  - 路由 `GET /api/v1/documents`,接受 `tenant` / `status` / `keyword` /
    `department` / `doc_type` 过滤与 `limit` / `offset` 分页,返回 `total`;
  - **默认只列 `is_latest = true` 的文档**(退位旧版本与已删除文档不出现),
    提供参数让想看历史的能看;
  - 默认排序 `created_at` 倒序。
  这是"前端不改后端"边界的一处**显式例外**:该边界原为规避 CORS 与流式两件
  真麻烦而设,补一条只读列表端点性质不同,且属后端能力的真实缺口——知识库
  服务不能列举自己的文档,而 `/stats` 已在做 `count(*)`,`list` 是同一张表
  同一套过滤条件的自然延伸。
- **前端开工前须逐页核对数据可得性**:本 ADR 的界面设计曾假设存在文档列表
  端点,核查后发现并不存在。"每个页面需要的数据在后端是否真的存在"属于
  事实,由实现前逐条核验,不由界面设想倒推。
