/**
 * 回答视图模型:把"一次问答"统一成界面渲染用的形状。
 *
 * **为什么要这层**:一次问答有两个来源 ——
 * - **本次提问**:`POST /api/v1/chat` 返回 `ChatAnswer`(带 `citations`,
 *   引用含文档名 / 标题 / 页码 / 分数);
 * - **回看历史**:`GET /api/v1/query-logs/{query_id}` 返回 `QueryLog`
 *   (带 `hits`,命中含切片定位与分数,**不含文档名**)。
 *
 * 两者都要渲染成同一屏"回答 + 引用 + 命中",故在此归一:引用按编号排列,
 * 缺失的字段留空(界面显示"—"),不假装有。
 *
 * 术语遵循 `GLOSSARY.md`:命中与引用是两回事 —— 引用是**进入回答证据**的
 * 那些,命中是检索到的全部。
 */
import type { ChatAnswer, Citation } from '@/types/qa'
import type { QueryHit, QueryLog } from '@/types/logs'

/** 一次问答在界面上的完整呈现。 */
export interface AnswerView {
  /** 问答标识:反馈按它挂靠,回看历史按它定位。 */
  query_id: string
  /** 原始问题。 */
  question: string
  /** 回答正文。 */
  answer: string
  /** 是否拒答。 */
  refused: boolean
  /** 拒答原因(稳定码)。 */
  refusal_reason: string | null
  /** 本次生效的检索条数。 */
  top_k: number
  /** 本次生效的过滤维度。 */
  filters: Record<string, string>
  /** 引用(进入回答证据的来源)。 */
  citations: Citation[]
  /** 检索命中(全部,含未进入证据的)。 */
  hits: QueryHit[]
  /** 端到端延迟(ms)。 */
  latency_ms: number
  /** 生成器种类。 */
  generator: string
  /** 是否来自答档缓存。 */
  cache_hit: boolean
}

/** 把本次提问的响应归一成回答视图。 */
export function answerViewOfChat(chat: ChatAnswer): AnswerView {
  return {
    query_id: chat.query_id,
    question: chat.question,
    answer: chat.answer,
    refused: chat.refused,
    refusal_reason: chat.refusal_reason,
    top_k: chat.top_k,
    filters: chat.filters,
    citations: chat.citations,
    // 本次提问的响应不带命中明细(命中在查询日志里),此处留空
    hits: [],
    latency_ms: chat.latency_ms,
    generator: chat.generator,
    cache_hit: chat.cache_hit,
  }
}

/**
 * 把历史查询日志归一成回答视图。
 *
 * **引用的来源**:查询日志的 `hits` 里标了 `used`(是否进入回答证据);
 * 界面把进入证据的命中当作引用呈现,其余作为"命中但未进入证据"列出。
 *
 * **文档名缺失**:查询日志契约不含文档名,故 `file_name` 留 null ——
 * 界面据此显示"文档名不可查",不臆造一个名字(也不说"未知文档",
 * 那会把"查不到"说成"没有")。
 */
export function answerViewOfQueryLog(log: QueryLog): AnswerView {
  const citations: Citation[] = log.hits
    .filter((hit) => hit.used)
    .map((hit, position) => ({
      index: position + 1,
      chunk_id: hit.chunk_id,
      document_id: hit.document_id,
      file_name: null,
      title: hit.title,
      page_number: hit.page_number,
      score: hit.score,
    }))

  return {
    query_id: log.query_id,
    question: log.question,
    answer: log.answer,
    refused: log.refused,
    refusal_reason: log.refusal_reason,
    top_k: log.top_k,
    filters: log.filters,
    citations,
    hits: log.hits,
    latency_ms: log.latency_ms,
    generator: log.generator,
    cache_hit: false,
  }
}
