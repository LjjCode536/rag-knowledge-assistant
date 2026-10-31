/**
 * 响应信封:所有 HTTP 响应的统一形状。
 *
 * 契约以端 OpenAPI(`/openapi.json`)为准,类型**手写**并以其为准 ——
 * 不自动生成,保持前端类型是契约的一个显式声明,避免生成物掩盖契约变化
 * (ADR-0015)。
 */
export interface ApiEnvelope<DataT> {
  /** 错误码:成功恒为 `OK`;界面判断成败依据 `code`,不只看 HTTP 状态码。 */
  code: string
  /** 提示文案:成功为 `ok`。 */
  message: string
  /** 业务数据:失败时恒为 `null`。 */
  data: DataT | null
  /** 错误详情:成功时恒为 `null`。 */
  details: Record<string, unknown> | null
  /** 一次请求在全链路的唯一标识,经响应头、响应体与全部日志贯穿。 */
  trace_id: string
  /** 响应时刻。 */
  timestamp: string
}
