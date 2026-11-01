/**
 * 知识库文档端点的调用函数。
 *
 * 页面组件只调本层(或其它 `api/` 层模块)的函数,**不得直接发请求**。
 */
import type {
  DocumentList,
  DocumentListParams,
  DocumentView,
  ElementsView,
} from '@/types/documents'

import { request } from './client'

const DOCUMENTS_PATH = '/api/v1/documents'

/** 列出知识库文档,并给出符合筛选条件的总数。 */
export async function listDocuments(params: DocumentListParams = {}) {
  const result = await request<DocumentList>(DOCUMENTS_PATH, { query: { ...params } })
  return result.data
}

/** 按 id 查询文档状态(即入库进度)。 */
export async function getDocument(documentId: string) {
  const result = await request<DocumentView>(`${DOCUMENTS_PATH}/${documentId}`)
  return result.data
}

/** 查询解析产物(结构化元素):详情抽屉的"解析元素"块用。 */
export async function getDocumentElements(documentId: string) {
  const result = await request<ElementsView>(`${DOCUMENTS_PATH}/${documentId}/elements`)
  return result.data
}

/** 删除知识库文档(切片与向量连带清理)。 */
export async function deleteDocument(documentId: string) {
  const result = await request<DocumentView>(`${DOCUMENTS_PATH}/${documentId}`, {
    method: 'DELETE',
  })
  return result.data
}

/** 上传知识库文档:返回任务句柄(status 恒为 `uploaded`,入库进度须轮询)。 */
export async function uploadDocument(
  file: File,
  fields: { tenant?: string; department?: string; doc_type?: string; version?: string } = {},
) {
  const form = new FormData()
  form.append('file', file)
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== '') {
      form.append(key, value)
    }
  }
  const result = await request<DocumentView>(DOCUMENTS_PATH, {
    method: 'POST',
    body: form,
  })
  return result.data
}
