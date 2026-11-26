/**
 * 底层请求函数:**全站唯一一处发 HTTP 请求的地方**(ADR-0015)。
 *
 * 职责固定为四步:发请求 → 拆响应信封 → 按错误码抛类型化错误 → 带上
 * trace_id。页面组件只调 `api/` 层的函数,不得直接发请求。
 *
 * 两条契约陷阱在此收口:
 *
 * - **界面判断成败依据 `code`,不只看 HTTP 状态码** —— 后端成功与失败
 *   同形(响应信封),失败时 HTTP 状态码与 `code` 一致,但业务错误仍以
 *   `code` 为准;
 * - **CSV 导出端点破坏响应信封** —— `GET /api/v1/feedback/negative/candidates?format=csv`
 *   返回带 BOM 的裸 CSV,该端点**不走**本文件的信封拆解,由
 *   `requestRaw` 单独处理。
 */
import { getAuthToken, notifyUnauthorized } from '@/auth/session'
import type { ApiEnvelope } from '@/types/envelope'

import { ApiError } from './errors'

/** 成功错误码:后端响应信封的成功态恒为此值。 */
export const OK_CODE = 'OK'

/** 请求选项:与 `fetch` 的差异是路径与查询参数由本层拼装。 */
export interface RequestOptions {
  /** 查询参数;`undefined` / `null` 的项不参与拼装。 */
  query?: Record<string, string | number | boolean | undefined | null>
  /** 请求体(对象自动 JSON 序列化)。 */
  body?: unknown
  /** 额外的请求头。 */
  headers?: Record<string, string>
  /** 请求方法,缺省按有无 body 推断。 */
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  /** `AbortSignal`:用于取消请求。 */
  signal?: AbortSignal
}

/** 拆出的信封结果:数据与 trace_id 一并返回。 */
export interface EnvelopeResult<DataT> {
  data: DataT
  traceId: string
}

/**
 * 拼装查询串:跳过 `undefined` / `null` / 空串的项。
 *
 * 空串按"未提供"处理(与后端路由层的空值归一口径一致):前端表单里
 * 清空的输入框不应变成 `?keyword=` 这样的"匹配空字符串"约束。
 *
 * **`false` 不跳过**:`false` 是一个有意义的布尔取值(如 `include_history=false`
 * 表示"只列现行版本"),与"没传"不是一回事 —— 后端两者的语义不同,省掉
 * `false` 会让"显式关闭"退化成"用后端默认值"。
 */
export function buildQuery(
  query: RequestOptions['query'],
): string {
  if (!query) {
    return ''
  }
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') {
      continue
    }
    params.append(key, String(value))
  }
  const serialized = params.toString()
  return serialized ? `?${serialized}` : ''
}

/** 发请求并拆信封:成功返回数据,失败抛 `ApiError`。 */
export async function request<DataT>(
  path: string,
  options: RequestOptions = {},
): Promise<EnvelopeResult<DataT>> {
  const url = `${path}${buildQuery(options.query)}`
  const method = options.method ?? (options.body === undefined ? 'GET' : 'POST')
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const token = getAuthToken()

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers: {
        Accept: 'application/json',
        // 统一注入凭证:页面只调 api/ 层函数,不各自拼 Authorization
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(isFormData || options.body === undefined
          ? {}
          : { 'Content-Type': 'application/json' }),
        ...options.headers,
      },
      body:
        options.body === undefined
          ? undefined
          : isFormData
            ? (options.body as FormData)
            : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (cause) {
    // 网络层失败(后端没起 / 连接被拒):给一个可辨认的错误码,便于界面
    // 呈现"服务不可达"而不是笼统的未知错误
    throw new ApiError({
      code: 'NETWORK_ERROR',
      message: '无法连接后端服务,请确认后端已启动',
      details: { cause: String(cause) },
      status: 0,
    })
  }

  handleExpiredSession(response)
  return parseEnvelope<DataT>(response)
}

/**
 * 401 会话失效:原本带着 token 的请求被拒才清理;登录失败(本来没 token)
 * 不清也不跳转。客户端层只负责清凭证与通知,回登录页由应用外壳决定。
 */
function handleExpiredSession(response: Response): void {
  if (response.status === 401 && getAuthToken()) {
    notifyUnauthorized()
  }
}

/** 解析响应信封:按 `code` 判成败,失败抛 `ApiError`(带上 trace_id)。 */
export async function parseEnvelope<DataT>(response: Response): Promise<EnvelopeResult<DataT>> {
  let payload: ApiEnvelope<DataT>
  try {
    payload = (await response.json()) as ApiEnvelope<DataT>
  } catch {
    // 响应体不是 JSON:后端异常时可能返回 HTML 错误页
    throw new ApiError({
      code: 'INVALID_RESPONSE',
      message: `后端返回了非预期的响应内容(HTTP ${response.status})`,
      status: response.status,
    })
  }

  if (payload.code !== OK_CODE) {
    throw new ApiError({
      code: payload.code,
      message: payload.message,
      details: payload.details,
      traceId: payload.trace_id,
      status: response.status,
    })
  }

  return { data: payload.data as DataT, traceId: payload.trace_id }
}

/**
 * 发请求并取原始文本:给**破坏响应信封**的端点用(CSV 导出)。
 *
 * 该端点返回带 UTF-8 BOM 的裸 CSV,不经过信封拆解;失败时仍按 HTTP
 * 状态码抛 `ApiError`(它没有信封可拆)。
 *
 * **BOM 要自己加回去**:`Response.text()` 按 WHATWG 规范会剥掉 UTF-8 BOM,
 * 而后端特意加这个前缀是为了让 Excel 直开中文。走 `arrayBuffer()` 拿原始
 * 字节再解码,BOM 得以保留 —— 否则导出的文件在 Excel 里是乱码。
 */
export async function requestRaw(
  path: string,
  options: RequestOptions = {},
): Promise<string> {
  const url = `${path}${buildQuery(options.query)}`
  const method = options.method ?? 'GET'
  const token = getAuthToken()
  const response = await fetch(url, {
    method,
    headers: {
      Accept: 'text/csv, */*',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
    body:
      options.body === undefined
        ? undefined
        : JSON.stringify(options.body),
    signal: options.signal,
  })
  if (!response.ok) {
    handleExpiredSession(response)
    throw new ApiError({
      code: `HTTP_${response.status}`,
      message: `导出失败(HTTP ${response.status})`,
      status: response.status,
    })
  }
  const buffer = await response.arrayBuffer()
  // `ignoreBOM: true` 让解码器**保留** BOM(默认行为是剥掉它)
  return new TextDecoder('utf-8', { ignoreBOM: true }).decode(buffer)
}
