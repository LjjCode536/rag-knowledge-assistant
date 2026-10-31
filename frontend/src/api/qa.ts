/**
 * 问答与检索端点的调用函数。
 *
 * 页面组件只调本层(或其它 `api/` 层模块)的函数,**不得直接发请求**。
 */
import type {
  ChatAnswer,
  ChunkFilter,
  ChunksList,
  EmbeddingRun,
  SearchResult,
} from '@/types/qa'

import { request } from './client'

/**
 * 提问:得到有据回答与引用;资料不足时后端统一拒答。
 *
 * 后端 `/chat` 是单次缓冲 JSON(无流式):第一版界面用"请求完成后打字机
 * 动画"的假流式,总时长与后端真实耗时相同,只改善观感(ADR-0015)。
 */
export async function ask(
  question: string,
  options: { top_k?: number; filters?: ChunkFilter } = {},
) {
  const result = await request<ChatAnswer>('/api/v1/chat', {
    method: 'POST',
    body: {
      question,
      top_k: options.top_k,
      filters: options.filters,
    },
  })
  return result.data
}

/** 相似度检索:命中切片带分数与完整元数据。 */
export async function search(
  query: string,
  options: { top_k?: number; filters?: ChunkFilter } = {},
) {
  const result = await request<SearchResult>('/api/v1/search', {
    method: 'POST',
    body: {
      query,
      top_k: options.top_k ?? 5,
      filters: options.filters,
    },
  })
  return result.data
}

/** 查询某文档的全部切片(含告警)。 */
export async function listChunks(documentId: string) {
  const result = await request<ChunksList>(`/api/v1/documents/${documentId}/chunks`)
  return result.data
}

/** 重试向量化:文档停在 `chunked` 时可单独重试,不必重传整份知识库文档。 */
export async function retryEmbedding(documentId: string) {
  const result = await request<EmbeddingRun>(`/api/v1/documents/${documentId}/embeddings`, {
    method: 'POST',
  })
  return result.data
}
