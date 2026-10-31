# 迭代 0010:T18 前端工程骨架与 API 客户端层

**工单**:issue #20(T18,父 #18) · **ADR**:ADR-0015(前端栈与边界)

## 背景与可控指标

后端 M0–M7 已收口(T16),但对外形态是纯 JSON API —— 9 个路由模块,能力只能靠
`curl` 与 Swagger 手工验证:有据回答与引用、入库状态机、运营统计与成本统计、
反馈闭环、高级检索的内部过程,全都没有可见出口。

本迭代交付**后端能力的第一个可见外壳**,也是后续所有前端工作的地基。

本迭代的可控指标:

1. **能跑起来**:装依赖 → 启动开发服务器 → 浏览器渲染 → 零控制台报错;
2. **接得上后端**:同源调用,后端**零改动、零 CORS**;
3. **页面齐**:六个页面各有独立路由,侧边栏可导航,问答是默认首页,刷新后路由保持;
4. **收口一处**:HTTP 调用全站唯一入口,页面组件不出现直接请求;
5. **错误可见**:错误码 / 消息 / 详情 / trace_id 呈现,trace_id 可复制;
6. **门槛对等**:typecheck 零错误、lint 零 error、测试全绿、生产构建成功。

## 做法

- **独立工程,置于 `frontend/` 子目录**,自带 `package.json` 与工具链(pnpm),
  **不进入 Python 依赖体系**:`uv` / `pyproject.toml` 不感知前端,两套依赖互不
  污染;后端 `uv run pytest` 不跑前端测试。
- **栈**:Vue 3.5 + TypeScript + Vite 8 + Element Plus 2.14 + vue-router 5 +
  Pinia 4 + TanStack Query 5;图表用 ECharts 6 + vue-echarts 8。
- **接入方式**:开发期用 Vite dev server 的 proxy 把 `/api` 与 `/health` 转发到
  后端 `127.0.0.1:8000`,**后端零改动、零 CORS**;生产期用同一域名反代同时服务
  静态产物与接口。前端**不挂进 FastAPI**(不 `StaticFiles`、不 `Jinja2Templates`)。
- **请求收口一处**:`api/client.ts` 的 `request` / `requestRaw` 是全站唯一发请求
  处,职责固定为四步:发请求 → 拆响应信封 → 按错误码抛类型化错误 → 带上
  `trace_id`。**页面组件只调 `api/` 层的函数**。
- **类型以契约为准且手写**:`types/` 下的类型是后端 OpenAPI 契约的**显式声明**,
  不自动生成 —— 生成物会掩盖契约变化。
- **状态分层**:服务端状态(查询、统计、文档档案、轮询)归 TanStack Query;
  跨页面客户端状态(当前租户筛选)归 Pinia;组件局部状态用 `ref` / `reactive`。
- **Element Plus 按需引入** + SCSS 变量覆盖为企业蓝主题,**不引入第二套样式体系**。

## 真机验证(浏览器实测)

后端(`uvicorn`)与前端(`vite dev`)同时起,浏览器逐页实测:

| 验收项 | 实测结果 |
| --- | --- |
| 开发服务器渲染 | ✅ 侧边栏 / 租户选择器 / 六个菜单项齐备,零控制台报错 |
| 同源转发,后端零 CORS | ✅ `GET /health`、`GET /api/v1/documents`、`POST /api/v1/chat` 全部经 proxy 转发;后端日志每条带 `trace_id`,**无任何跨源预检** |
| 六页可导航 | ✅ 问答 / 知识库文档 / 检索 / 运营统计 / 查询日志 / 反馈,逐页实测无报错 |
| 问答为默认首页 | ✅ 访问 `/` 即问答页 |
| 刷新后路由保持 | ✅ `/feedback` 刷新后 URL 不变、页面照常渲染 |
| 文档清单(消费 T17 新端点) | ✅ 表格显示 `handbook.md \| 已入库 \| acme \| 1 切片`,`Total 1` |
| 有据回答 + 引用 | ✅ 「根据资料[1]，员工入职满一年后可以申请年假。」+ 引用列表 `handbook.md · 休假制度 · 分数 0.909039` |
| 运营统计 | ✅ 文档数 1 / 已索引 1 / 切片 1 / 问答量 2 / 拒答率 0 / 平均延迟 / 估算成本 / 分桶趋势,全部真实数据 |
| 查询日志 | ✅ 两条真实问答记录(延迟 33283.98 ms 与 0.09 ms) |
| 窄屏侧边栏折叠 | ✅ 侧边栏消失、出现「菜单」按钮、抽屉可打开、内容区 `scrollWidth == clientWidth`(无横向溢出) |

真实上游(SiliconFlow)在实测中确实被调用:`BAAI/bge-m3` 向量化 1400 ms、
`Qwen/Qwen3-8B` 生成 19861 ms、`BAAI/bge-reranker-v2-m3` 重排 85 ms。

## 验收(issue #20 逐条)

- [x] 前端工程装依赖后可启动开发服务器,浏览器打开能渲染,无控制台报错 ——
      上表「真机验证」
- [x] 开发服务器把接口请求与健康检查请求转发到后端,前端在同源下调用,
      **后端无需任何跨源配置** —— `vite.config.ts` 的 `server.proxy` + 真机实测
- [x] 六个页面各有独立路由,左侧边栏可导航,问答为默认首页,刷新后路由保持 ——
      `src/router/index.ts` + 真机实测
- [x] 响应信封的拆解收口在 `api/` 层唯一一处:页面组件代码里不出现直接的请求调用
      —— `src/api/client.ts` 是全站唯一 `fetch` 调用点
- [x] 后端返回非成功错误码时,`api/` 层抛出带错误码 / 消息 / 详情 / trace_id 的
      错误对象 —— `src/api/errors.ts` 的 `ApiError`;
      `src/api/client.spec.ts` 的「错误码映射到带 trace_id 的类型化错误」
- [x] trace_id 能在界面上被看到并复制(错误详情区域) —— `src/components/ErrorDetails.vue`
      (带「复制」按钮);组件测试「展示错误码、消息与可复制的 trace_id」
- [x] 全局租户选择器可用(可选、可新建),选择结果在页面间保持 ——
      `src/stores/tenant.ts`(Pinia)+ `src/components/TenantSelector.vue`
- [x] 主题为企业蓝,Element Plus 组件按需引入,未引入第二套样式体系 ——
      `src/styles/theme.scss`(SCSS 变量覆盖 `primary: #1f5eff`),未引入 Tailwind
- [x] 窄屏下侧边栏折叠为抽屉,主要内容区不横向溢出 —— 真机实测(480px 宽)
- [x] typecheck 零错误、lint 零 error、构建成功 —— `pnpm typecheck` / `pnpm lint`
      / `pnpm build` 全绿
- [x] API 客户端层有测试:断言信封拆解取到数据、断言错误码映射到带 trace_id 的
      错误、断言请求参数与后端契约一致 —— `src/api/client.spec.ts` 12 例
- [x] 前端工程不进入 Python 依赖体系(`uv` / `pyproject.toml` 不感知前端),
      两套依赖互不污染 —— `pyproject.toml` 无任何前端依赖;`uv run pytest`
      不跑前端测试

### 质量门槛实测

| 门槛 | 命令 | 结果 |
| --- | --- | --- |
| 类型检查 | `pnpm typecheck` | 零错误 |
| Lint | `pnpm lint` | 零 error(`--max-warnings 0`) |
| 单元与组件测试 | `pnpm test` | 19 passed(API 客户端层 12 + 组件行为 7) |
| 生产构建 | `pnpm build` | 成功(3.27s) |
| 后端回归 | `uv run pytest` | 404 passed / 0 skipped |

## 实现中发现并修掉的两处真问题

1. **CSV 导出的 BOM 被吞掉(真 bug)**:`Response.text()` 按 WHATWG 规范会**剥掉**
   UTF-8 BOM,而 `TextDecoder('utf-8')` 默认也剥 —— 而后端特意加这个前缀是为了让
   Excel 直开中文。照原样实现的话,导出的差评候选 CSV 在 Excel 里是乱码。
   改走 `arrayBuffer()` + `TextDecoder('utf-8', { ignoreBOM: true })`,BOM 得以保留。
   测试守住:`src/api/client.spec.ts` 的「CSV 端点返回原始文本而不走信封拆解」。
2. **`isApiError` 只靠 `instanceof` 会在跨 realm 时失效(真 bug)**:构建产物里同一份
   代码可能被加载成多个 realm(微前端、测试的模块图、SSR),`instanceof` 在那种情况下
   判不出,错误对象明明是 `ApiError`,界面却会把它当未知错误处理,**丢掉 `code` 与
   `trace_id`**。改为按结构判定(是错误对象且带字符串 `code` 字段)。

另有一处 dev server 的踩坑记录在案:按需引入的 Element Plus 组件在**首次访问
页面**时才被依赖优化器发现,会触发「优化 → 强制 reload → 打断路由的动态导入」,
页面首访白屏。修法是在 `vite.config.ts` 的 `optimizeDeps.include` 里显式预打包
`element-plus`,让优化在 dev server 启动时一次做完。

## 口径(ADR-0015)

- **前端是后端能力的真实视图**:每个界面元素都能追溯到一个后端端点与一个既有
  领域概念;后端做不到的事(鉴权、流式、完整分页历史),界面不假装能做到。
- **租户是筛选维度,不是权限控制**:后端当前没有用户 / 会话 / 鉴权,任何调用方都能
  传任意 `tenant`;界面如实呈现这一现状,不做假登录制造"已隔离"的错觉。
- **假流式不改善真实延迟**:第一版用"请求完成后前端打字机动画",界面总时长与后端
  真实耗时相同,只是观感;首字节时间(TTFB)仍是后端全量响应时间。
- **前端类型以契约为准且手写**:契约由后端 OpenAPI 定义,前端类型手写并以其为准,
  不自动生成。
- **入库进度依赖轮询,不是推送**:进度查询的实时性受轮询间隔限制;`embedding`
  状态是请求内瞬时态,轮询大概率观察不到。

## 已知边界与后续

- **租户选项的数据源后接**:本次的全局租户选择器已立起来(可选、可新建、页面间
  保持),但选项集目前只有本地新建的条目;真实租户集合应从后端文档清单返回的
  租户维度聚合(`stores/tenant.ts` 的 `replaceOptions` 已备好接口),属后续迭代。
- **图表未接入 ECharts**:ECharts + vue-echarts 已进依赖,运营统计页当前用表格
  呈现分桶趋势;接图表库是纯前端迭代,不涉及后端。
- **上传交互只到"选文件 + 扩展名校验 + 提交"**:拖拽区、重试向量化入口、删除前
  的连带清理提示等未做 —— 骨架阶段的取舍。
- **端到端(Playwright)不在本次范围**,另开迭代;本次的浏览器验证是手工实测,
  未固化为自动化用例。
- **窄屏是基础响应式**:侧边栏折叠 + 表格横向滚动,不做完整移动端布局。
