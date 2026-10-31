/**
 * 类型化错误:后端返回非成功错误码时由 `api/` 层抛出。
 *
 * 错误对象带错误码 / 消息 / 详情 / trace_id —— 界面按 `code` 决定呈现
 * 方式,并把 `trace_id` 展示出来供维护者精确复现当次请求
 * (呼应后端"trace_id 贯穿响应头、响应体与全部日志"的可观测性契约)。
 */

export class ApiError extends Error {
  /** 错误码:与后端 `ErrorCode` 一一映射的稳定标识。 */
  readonly code: string
  /** 错误详情:后端 `details` 字段,可能为 `null`。 */
  readonly details: Record<string, unknown> | null
  /** 一次请求在全链路的唯一标识。 */
  readonly traceId: string
  /** HTTP 状态码(网络层失败时为 0)。 */
  readonly status: number

  constructor(init: {
    code: string
    message: string
    details?: Record<string, unknown> | null
    traceId?: string
    status?: number
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.code = init.code
    this.details = init.details ?? null
    this.traceId = init.traceId ?? ''
    this.status = init.status ?? 0
  }
}

/**
 * 判定一个值是不是 `ApiError`。
 *
 * 不只靠 `instanceof`:构建产物里同一份代码可能被加载成多个 realm
 * (微前端、测试的模块图、SSR),`instanceof` 在跨 realm 时会失效 ——
 * 那时错误对象明明是 `ApiError`,界面却会把它当未知错误处理,丢掉
 * `code` 与 `trace_id`。因此按**结构**判定:是错误对象且带 `code` 字段。
 */
export function isApiError(value: unknown): value is ApiError {
  if (value instanceof ApiError) {
    return true
  }
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    typeof (value as { code: unknown }).code === 'string'
  )
}
