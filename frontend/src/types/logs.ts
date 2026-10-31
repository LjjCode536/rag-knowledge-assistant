/**
 * 查询日志与回答反馈的类型:契约以端 OpenAPI(`/openapi.json`)为准。
 *
 * **服务端分页的已知上限**:查询日志与反馈两个端点只有 `limit` 没有
 * `offset`,界面取最近 100 条做前端分页并**明确标注该上限**(ADR-0015)。
 */
import type { Citation, GenerationUsage, GeneratorKind, RefusalReason } from './qa'

/** 命中:排名、切片定位、分数与是否进入回答证据。 */
export interface QueryHit {
  rank: number
  chunk_id: string
  document_id: string
  title: string | null
  page_number: number | null
  score: number
  used: boolean
}

/** 查询日志:问题、答案、拒答原因、命中、Token 用量与端到端延迟。 */
export interface QueryLog {
  query_id: string
  trace_id: string
  question: string
  answer: string
  refused: boolean
  refusal_reason: RefusalReason | null
  hits: QueryHit[]
  filters: Record<string, string>
  top_k: number
  min_score: number
  latency_ms: number
  generator: GeneratorKind
  token_usage: GenerationUsage
  rewritten_question: string | null
  expanded_questions: string[]
  created_at: string
}

/** 查询日志列表:总数 + 当前页条数。 */
export interface QueryLogs {
  total: number
  limit: number
  items: QueryLog[]
}

/** 回答反馈判定:有用 / 无用。 */
export type FeedbackVerdict = 'useful' | 'useless'

/** 回答反馈:经 query_id 关联查询日志,同一问答重复提交按现行反馈覆盖。 */
export interface Feedback {
  feedback_id: string
  query_id: string
  trace_id: string
  verdict: FeedbackVerdict
  comment: string | null
  created_at: string
}

/** 反馈列表:总数 + 当前页条数。 */
export interface FeedbackList {
  total: number
  limit: number
  items: Feedback[]
}

/** 差评来源:差评落在哪次问答上,答案当时长什么样。 */
export interface FeedbackOrigin {
  query_id: string
  trace_id: string
  verdict: FeedbackVerdict
  refused: boolean
  refusal_reason: RefusalReason | null
  top_documents: string[]
  comment: string | null
  created_at: string
}

/** 黄金用例候选:同问题差评聚类出的待改进项。 */
export interface GoldenCaseCandidate {
  id: string
  question: string
  occurrences: number
  failure: string
  first_seen: string
  last_seen: string
  comments: string[]
  origins: FeedbackOrigin[]
}

/** 差评候选清单:`total` 是差评反馈总条数,同问题差评聚成一组。 */
export interface NegativeCandidates {
  total: number
  limit: number
  items: GoldenCaseCandidate[]
}

export type { Citation }
