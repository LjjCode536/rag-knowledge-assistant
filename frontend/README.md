# 前端工作台(Vue 3 SPA)

> 企业 AI 知识库的前端外壳。术语遵循仓库根目录 `GLOSSARY.md`;架构决策见
> [ADR-0015](../docs/adr/0015-frontend-stack-and-boundary.md)。

## 这是什么

一个**能跑起来的前端工程**:装上依赖、启动开发服务器、连上后端,登录后六个业务页
可导航。这是后端能力的第一个可见外壳。

**核心原则:前端是后端能力的真实视图。** 每个界面元素都能追溯到一个后端
端点与一个既有领域概念;后端做不到的事(流式、完整分页历史),界面不
假装能做到。登录、租户与文档级授权都来自服务端身份,前端不做本地权限判断
来代替服务端强制。

## 命令

```bash
pnpm install        # 安装依赖
pnpm dev            # 启动开发服务器(http://localhost:5173)
pnpm build          # 生产构建(产物在 dist/)
pnpm preview        # 预览生产构建
pnpm typecheck      # 类型检查(vue-tsc,零错误)
pnpm lint           # 代码检查(eslint,零 error)
pnpm test           # 单元与组件测试(vitest)
pnpm test:watch     # 测试监听模式
```

本工程**不进入 Python 依赖体系**:`uv` / `pyproject.toml` 不感知前端,两套
依赖互不污染。后端的 `uv run pytest` 也不会跑前端测试。

## 与后端的接入方式

**开发期用 Vite dev server 的 proxy 打通后端**,`/api` 与 `/health` 转发到
`127.0.0.1:8000`,**后端零改动、零 CORS** —— 浏览器视角下前端与接口同源。

后端端口不同时,用环境变量覆盖:

```bash
VITE_BACKEND_ORIGIN=http://127.0.0.1:9000 pnpm dev
```

**生产期用同一域名反代同时服务静态产物与接口**(nginx / Caddy),后端仍然
不需要 CORS。前端**不挂进 FastAPI**:后端保持"只提供 JSON API"的单一职责。

本地开发要同时起两个进程,并先准备好可登录的种子账号(见根目录 README
「快速开始」:账号只落 PostgreSQL,内存模式不建号):

```bash
# 终端 1:后端(先拉起数据库与种子账号;登录需要 PG)
VECTOR_STORE=pgvector uv run python -m uvicorn app.asgi:app --reload

# 终端 2:前端
cd frontend && pnpm dev
```

浏览器打开 `http://localhost:5173` 会先到登录页,用种子账号(如 `member`)
登录;token 存内存 + `sessionStorage`,过期或服务端返回 401 时清理并回登录页。

## 技术栈

| 关注点 | 选型 |
| --- | --- |
| 框架 | Vue 3.5 + TypeScript |
| 构建 | Vite 8 |
| UI | Element Plus 2.14(按需引入 + 企业蓝主题) |
| 路由 | vue-router 5 |
| 服务端状态 | TanStack Query(vue-query)5 |
| 跨页面状态 | Pinia 4 |
| 图表 | ECharts 6 + vue-echarts 8 |
| 测试 | Vitest 5 + @vue/test-utils |

样式以 Element Plus 自带设计语言为基底,只用 SCSS 变量做主题定制
(企业蓝主色),**不引入 Tailwind** —— 避免两套体系混用。Element Plus 按需引入,
并装官方 `zh-cn` 语言包(分页 / 表格 / 选择器的内置文案走中文)。

## 目录结构

```
frontend/
├── src/
│   ├── api/          # HTTP 调用收口层(全站唯一发请求处,统一注入 token)
│   ├── auth/         # 凭证存取(sessionStorage)与会话失效通知
│   ├── components/   # 跨页面复用组件
│   ├── composables/  # 组合式函数(筛选维度等)
│   ├── router/       # 路由、守卫与侧边栏导航定义
│   ├── stores/       # Pinia(当前主体 / 登录态)
│   ├── styles/       # 主题与基础样式
│   ├── types/        # 手写的响应类型(以契约为准)
│   ├── views/        # 登录页 + 六个业务页
│   ├── App.vue       # 应用外壳(侧边栏 + 标题栏 + 主体信息 + 内容区)
│   └── main.ts       # 应用入口
├── index.html
├── vite.config.ts
└── package.json
```

## 页面

| 路由 | 页面 | 消费的后端端点 |
| --- | --- | --- |
| `/login` | 登录 | `POST /api/v1/auth/login` |
| `/` | 问答(默认首页) | `POST /api/v1/chat`、`GET /api/v1/feedback`、`POST /api/v1/feedback` |
| `/documents` | 知识库文档 | `GET /api/v1/documents`、`POST /api/v1/documents`、`DELETE /api/v1/documents/{id}`、文档详情里的授权清单端点 |
| `/search` | 检索 | `POST /api/v1/search` |
| `/stats` | 运营统计 | `GET /api/v1/stats`、`GET /api/v1/stats/cost` |
| `/query-logs` | 查询日志 | `GET /api/v1/query-logs` |
| `/feedback` | 反馈 | `GET /api/v1/feedback`、`GET /api/v1/feedback/negative/candidates` |

登录页之外的路由都经过守卫:没有 token 就回登录页;刷新后先用
`GET /api/v1/auth/me` 恢复主体,401 时清理并回登录页。

## 两条硬纪律

### 1. 前端不得直接发 HTTP 请求

所有 HTTP 调用经过 `api/` 层**唯一的底层请求函数**(`api/client.ts` 的
`request` / `requestRaw`),职责固定为四步:

1. 发请求;
2. 拆响应信封(`code` / `message` / `data` / `details` / `trace_id` / `timestamp`);
3. 按错误码抛类型化错误(`ApiError`,带 `code` / `message` / `details` / `trace_id`);
4. 把 `trace_id` 带出来。

**页面组件只调 `api/` 层的函数。** 组件里出现 `fetch` / `axios` 即为违规。

### 2. 界面文案遵循 `GLOSSARY.md`

界面文案与代码标识符使用规范术语:知识库文档 / 切片 / 引用 / 查询日志 /
拒答 / 入库进度。**不使用** `_Avoid_` 清单里的同义词:文件、片段、source、
出处、分段、评价、点赞。

例如表格列头写「文档名」而不是「文件名」,表单标签写「知识库文档」而不是
「文件」—— 后端字段名 `file_name` 照原样透传(那是契约),但**界面文案**不跟着
用同义词。

## 状态分层

| 状态 | 归属 |
| --- | --- |
| 服务端状态(查询、统计、文档档案、轮询) | TanStack Query |
| 跨页面客户端状态(登录态、当前主体) | Pinia |
| 组件局部状态 | `ref` / `reactive` |

入库进度用 TanStack Query 的轮询能力表达,**不手写** `setInterval` 与
生命周期清理。

## 已知契约陷阱(前端必须逐条处理)

- **响应信封**:成功与失败同形。**界面判断成败依据 `code`**,不只看 HTTP
  状态码;成功时 `details` 恒为 `null`,失败时 `data` 恒为 `null`。
- **上传响应是任务句柄**:异步形态下上传响应里的 `status` 恒为 `uploaded`,
  元素数 / 页数 / 切片数 / 各类时间戳都是 `null`。入库进度靠轮询档案查询。
- **切片失败不返回错误码**:切片运行时失败返回 HTTP 200 但
  `document.status == "failed"` 且带失败原因。界面必须检查文档状态。
- **`embedding` 是请求内瞬时态**:轮询大概率观察不到,界面不应把它当作
  "必经的一站"来渲染进度异常。
- **CSV 导出破坏响应信封**:`GET /api/v1/feedback/negative/candidates?format=csv`
  返回带 UTF-8 BOM 的裸 CSV,该端点**不走**统一拆解逻辑
  (`requestRaw`),且 BOM 需自己保留(`Response.text()` 会剥掉它)。
- **无样本是 `null` 不是 0**:拒答率与平均延迟在问答量为 0 时为 `null`,
  界面显示"无样本"。
- **反馈按 `query_id` 查询时的 `total`** 是本次返回条数(0 或 1),不是全库计数。
- **查询日志与反馈只有 `limit` 没有 `offset`**:界面取最近 100 条做前端分页,
  并明确标注该上限。
- **租户不是请求参数**:普通端点携带 `tenant` 查询参数返回
  `403 SCOPE_VIOLATION`;租户、角色与部门只来自登录身份,界面不提供切换。
- **跨租户 / 无权限对象是 404**:别用 403 区分"存在但无权",界面统一按
  "不存在或无权查看"呈现。
- **授权清单只对所有者 / 管理员可写**:普通成员能看文档(有授权时)但看不到
  管理按钮;按钮隐藏只是体验,服务端仍会拒绝越权写。

## 测试

**只测外部行为,不测实现细节。** 前端代码内部可以随意重构,只要用户可见的
行为不变,测试就不应改动。

- **接缝 A:API 客户端层**(`src/api/client.spec.ts`)—— 断言信封拆解取到
  数据、错误码映射到带 `trace_id` 的错误、CSV 端点返回原始文本、token 注入
  与 401 清理、请求参数与后端契约一致。
- **接缝 B:组件行为**(`src/views/views.spec.ts`、`src/views/documentsView.spec.ts`、
  `src/views/opsViews.spec.ts`、`src/views/loginView.spec.ts`)—— 用
  `@vue/test-utils` 挂载组件,断言用户可见的行为:登录表单、未登录跳转、
  授权清单仅对所有者 / 管理员可见可操作、有据回答渲染引用、拒答时**不渲染**
  引用列表、反馈状态回显、状态标签按状态呈现、上传前格式校验、入库进度、
  检索过滤与统计区间实际传参、无样本显示「无样本」、查询日志 / 差评候选字段、
  CSV 不走信封拆解、错误详情展示可复制的 `trace_id`。

替身:组件测试不真打后端,用 `fetch` 拦截层注入固定的信封响应 —— 与后端
"以假 LLM / 假向量库替身替换真实上游"是同一思路。

端到端(Playwright)不在本次范围,另开迭代。

## 边界(ADR-0015 / ADR-0020)

- **登录是演示级存储**:token 存内存 + `sessionStorage`,无 refresh token,
  过期即重新登录。生产应换 OIDC 授权码 + PKCE 与 HttpOnly 会话;前端不把
  这一实现当作生产级会话。
- **前端不做权限判断代替服务端**:租户、角色与部门来自 token,界面隐藏按钮
  只为体验;真正的边界在服务端(401 / 403 / 404 与审计)。
- **不引入流式**:后端 `/chat` 是单次缓冲 JSON。第一版用"请求完成后前端打字机
  动画"的**假流式**;假流式不改善真实延迟,只改善观感。
