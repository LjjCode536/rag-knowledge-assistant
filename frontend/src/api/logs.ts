/**
 * 查询日志与回答反馈端点的调用函数。
 *
 * **CSV 导出破坏响应信封**:`GET /api/v1/feedback/negative/candidates?format=csv`
 * 返回带 UTF-8 BOM 的裸 CSV,该端点**不走**统一信封拆解,由 `requestRaw`
 * 处理(见 `api/client.ts`)。
 */
import type {
  Feedback,
  FeedbackList,
  FeedbackVerdict,
  NegativeCandidates,
  QueryLog,
  QueryLogs,
} from '@/types/logs'

import { request, requestRaw } from './client'

/** 查询日志(最近优先)。注意:该端点只有 `limit`,没有 `offset`。 */
export async function listQueryLogs(limit = 20) {
  const result = await request<QueryLogs>('/api/v1/query-logs', { query: { limit } })
  return result.data
}

/** 按 query_id 查询单条查询日志。 */
export async function getQueryLog(queryId: string) {
  const result = await request<QueryLog>(`/api/v1/query-logs/${queryId}`)
  return result.data
}

/** 提交回答反馈;同一问答重复提交按现行反馈覆盖。 */
export async function submitFeedback(
  queryId: string,
  verdict: FeedbackVerdict,
  comment?: string,
) {
  const result = await request<Feedback>('/api/v1/feedback', {
    method: 'POST',
    body: { query_id: queryId, verdict, comment: comment ?? null },
  })
  return result.data
}

/**
 * 查询反馈列表。
 *
 * 按 `query_id` 查询时,后端返回的 `total` 是**本次返回条数**(0 或 1),
 * 不是全库计数 —— 界面据此判断"该次问答是否已反馈"。
 */
export async function listFeedback(
  options: { verdict?: FeedbackVerdict; query_id?: string; limit?: number } = {},
) {
  const result = await request<FeedbackList>('/api/v1/feedback', { query: { ...options } })
  return result.data
}

/** 差评候选清单(按问题聚类)。 */
export async function listNegativeCandidates(limit = 20) {
  const result = await request<NegativeCandidates>('/api/v1/feedback/negative/candidates', {
    query: { limit },
  })
  return result.data
}

/**
 * 导出差评候选为 CSV(原始文本)。
 *
 * **不走信封拆解**:该端点返回带 UTF-8 BOM 的裸 CSV。
 */
export async function exportNegativeCandidatesCsv(limit = 20) {
  return requestRaw('/api/v1/feedback/negative/candidates', {
    query: { limit, format: 'csv' },
  })
}
