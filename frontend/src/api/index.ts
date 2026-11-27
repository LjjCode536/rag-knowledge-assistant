/**
 * `api/` 层出口:页面组件只从这里 import,不得直接发请求。
 *
 * 全站唯一一处 HTTP 收口在 `api/client.ts` 的 `request` / `requestRaw`。
 */
export { ApiError, isApiError } from './errors'
export { buildQuery, request, requestRaw, OK_CODE } from './client'
export type { EnvelopeResult, RequestOptions } from './client'

export { getCurrentPrincipal, login } from './auth'
export {
  addDocumentGrant,
  deleteDocument,
  getDocument,
  getDocumentElements,
  listDocumentGrants,
  listDocuments,
  removeDocumentGrant,
  uploadDocument,
} from './documents'
export { ask, listChunks, retryEmbedding, search } from './qa'
export { getCost, getStats } from './stats'
export {
  exportNegativeCandidatesCsv,
  getQueryLog,
  listFeedback,
  listNegativeCandidates,
  listQueryLogs,
  submitFeedback,
} from './logs'
