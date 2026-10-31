/**
 * 运营统计与成本统计的类型:契约以端 OpenAPI(`/openapi.json`)为准。
 *
 * **没有样本是 `null` 不是 0**:问答量为 0 时拒答率与平均延迟为 `null`,
 * 界面必须显示"无样本"而非 0(GLOSSARY.md:拒答率)。
 */

/** Token 用量汇总:prompt / completion 分列。 */
export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

/** 估算成本:按配置单价折算的金额(不是上游账单金额)。 */
export interface CostSummary {
  prompt_cost: number
  completion_cost: number
  total_cost: number
  currency: string
}

/** 问答健康度:问答量、拒答率与平均延迟。 */
export interface QaStats {
  query_count: number
  refused_count: number
  refusal_rate: number | null
  avg_latency_ms: number | null
  token_usage: TokenUsage
}

/** 分桶:统计区间按固定粒度切成的时间片。 */
export interface StatsBucket {
  bucket_start: string
  bucket_end: string
  qa: QaStats
}

/** 运营统计一屏:知识库规模 + 问答健康度 + 成本 + 分桶序列。 */
export interface KnowledgeBaseStats {
  document_count: number
  indexed_document_count: number
  chunk_count: number
  qa: QaStats
  cost: CostSummary
  buckets: StatsBucket[]
  start: string | null
  end: string | null
  bucket_kind: string
}

/** 成本明细:Token 用量 + 估算成本 + 统计区间。 */
export interface CostReport {
  token_usage: TokenUsage
  cost: CostSummary
  start: string | null
  end: string | null
}
