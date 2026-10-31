/**
 * 检索与问答相关的类型:契约以端 OpenAPI(`/openapi.json`)为准,手写声明。
 *
 * 术语遵循 `GLOSSARY.md`:切片 / 引用 / 有据回答 / 拒答 / 查询日志。
 */

/** 检索前切片元数据过滤条件(权限维度):未给的字段一律放行。 */
export interface ChunkFilter {
  tenant?: string
  department?: string
  doc_type?: string
  version?: string
  document_id?: string
}

/** 切片视图:内容 + 完整切片元数据 + 来源定位。 */
export interface ChunkView {
  chunk_id: string
  document_id: string
  index: number
  content: string
  tenant: string
  department: string | null
  doc_type: string | null
  version: string
  page_number: number | null
  title: string | null
  element_index_start: number
  element_index_end: number
}

/** 检索命中:相似度分数 + 切片。 */
export interface SearchHit {
  score: number
  chunk: ChunkView
}

/** 检索结果:生效参数 + 按分数降序的命中。 */
export interface SearchResult {
  query: string
  top_k: number
  total: number
  filters: Record<string, string>
  hits: SearchHit[]
}

/** 引用:编号 + 来源切片定位(文档 / 标题 / 页码)+ 相似度分数。 */
export interface Citation {
  index: number
  chunk_id: string
  document_id: string
  file_name: string | null
  title: string | null
  page_number: number | null
  score: number
}

/**
 * 拒答原因:界面据此说明"为什么没能回答"。
 *
 * - `no_hits`:未检索到资料;
 * - `low_score`:检索到的资料相关度不足;
 * - `generator_refused`:生成器依据现有资料无法作答。
 */
export type RefusalReason = 'no_hits' | 'low_score' | 'generator_refused' | (string & {})

/** 生成器种类:规则版基线或真实 LLM。 */
export type GeneratorKind = 'rule_based' | 'llm' | (string & {})

/** Token 用量:一次问答在生成步消耗的 Token 数。 */
export interface GenerationUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

/** 一次问答:答案、拒答原因、引用、生成器留痕与端到端延迟。 */
export interface ChatAnswer {
  query_id: string
  question: string
  answer: string
  refused: boolean
  refusal_reason: RefusalReason | null
  top_k: number
  min_score: number
  filters: Record<string, string>
  citations: Citation[]
  latency_ms: number
  generator: GeneratorKind
  token_usage: GenerationUsage
  rewritten_question: string | null
  expanded_questions: string[]
  /** True 表示本次回答来自答档缓存(同题二次询问)。 */
  cache_hit: boolean
}

/** 切片质量告警:稳定码 + 文案 + 定位详情。 */
export interface ChunkQualityWarning {
  code: string
  message: string
  details: Record<string, unknown>
}

/** 某文档的全部切片:档案状态 + 切片与告警。 */
export interface ChunksList {
  document_id: string
  status: string
  chunk_count: number
  warnings: ChunkQualityWarning[]
  chunks: ChunkView[]
}

/** 一次向量化运行:档案最新状态 + 生效模型 + 用量与延迟。 */
export interface EmbeddingRun {
  document: {
    id: string
    status: string
    chunk_count: number | null
    [key: string]: unknown
  }
  model: string
  chunk_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  latency_ms: number
}
