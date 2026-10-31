/**
 * 接缝 A:API 客户端层的行为(issue #20)。
 *
 * 断言外部行为:成功信封能取出数据;`code != OK` 抛出带 code / message /
 * details / trace_id 的错误;CSV 端点返回原始文本而不走信封拆解;请求参数
 * 与后端契约一致。
 *
 * **不测**"调用了几次底层请求"这类实现细节。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import { buildQuery, request, requestRaw } from '@/api/client'
import { listDocuments, ask, search, submitFeedback } from '@/api'

/** 构造一个成功信封。 */
function okEnvelope<T>(data: T, traceId = 'trace-test-0001'): string {
  return JSON.stringify({
    code: 'OK',
    message: 'ok',
    data,
    details: null,
    trace_id: traceId,
    timestamp: '2026-10-31T08:00:00Z',
  })
}

/** 构造一个失败信封。 */
function errorEnvelope(
  code: string,
  message: string,
  details: Record<string, unknown> | null = null,
  traceId = 'trace-err-0001',
): string {
  return JSON.stringify({
    code,
    message,
    data: null,
    details,
    trace_id: traceId,
    timestamp: '2026-10-31T08:00:00Z',
  })
}

describe('api 客户端层', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('成功信封能取出数据与 trace_id', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(okEnvelope({ total: 1, items: [] }), { status: 200 }),
    )

    const result = await request<{ total: number }>('/api/v1/documents')

    expect(result.data).toEqual({ total: 1, items: [] })
    expect(result.traceId).toBe('trace-test-0001')
  })

  it('错误码映射到带 trace_id 的类型化错误', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        errorEnvelope('NOT_FOUND', '知识库文档不存在', { document_id: 'doc-1' }, 'trace-err-9'),
        { status: 404 },
      ),
    )

    const error = await request('/api/v1/documents/missing').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('NOT_FOUND')
    expect(apiError.message).toBe('知识库文档不存在')
    expect(apiError.details).toEqual({ document_id: 'doc-1' })
    expect(apiError.traceId).toBe('trace-err-9')
  })

  it('网络不可达时抛出可辨认的错误码', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('connection refused'))

    const error = await request('/api/v1/documents').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
    expect((error as ApiError).status).toBe(0)
  })

  it('响应体不是 JSON 时给出可辨认的错误码', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('<html>500 Internal Server Error</html>', { status: 500 }),
    )

    const error = await request('/api/v1/documents').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('INVALID_RESPONSE')
  })

  it('CSV 端点返回原始文本而不走信封拆解', async () => {
    const csv = '﻿id,question\ncand-1,年假怎么算?\n'
    vi.mocked(fetch).mockResolvedValue(new Response(csv, { status: 200 }))

    const text = await requestRaw('/api/v1/feedback/negative/candidates', {
      query: { format: 'csv' },
    })

    expect(text).toBe(csv)
    // 裸 CSV 不带信封字段:没有拆解出 data
    expect(text).not.toContain('"code"')
  })

  it('CSV 端点请求参数与后端契约一致', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('id\n', { status: 200 }))

    await requestRaw('/api/v1/feedback/negative/candidates', {
      query: { limit: 20, format: 'csv' },
    })

    const [url] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/v1/feedback/negative/candidates?limit=20&format=csv')
  })

  it('空值不参与查询串拼装(清空的输入框不构成约束)', () => {
    expect(buildQuery({ tenant: 'acme', keyword: '', status: undefined, page: null })).toBe(
      '?tenant=acme',
    )
    expect(buildQuery({})).toBe('')
    expect(buildQuery(undefined)).toBe('')
  })

  it('文档清单的请求参数与后端契约一致', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(okEnvelope({ total: 0, limit: 20, offset: 0, items: [] }), { status: 200 }),
    )

    await listDocuments({
      tenant: 'acme',
      keyword: '手册',
      status: 'indexed',
      include_history: true,
      limit: 20,
      offset: 40,
    })

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe(
      '/api/v1/documents?tenant=acme&keyword=%E6%89%8B%E5%86%8C&status=indexed&include_history=true&limit=20&offset=40',
    )
    expect(init?.method).toBe('GET')
  })

  it('问答请求体与后端契约一致', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(okEnvelope({}), { status: 200 }))

    await ask('年假怎么算?', { top_k: 3, filters: { tenant: 'acme' } })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      question: '年假怎么算?',
      top_k: 3,
      filters: { tenant: 'acme' },
    })
  })

  it('检索请求缺省 top_k 为 5', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(okEnvelope({}), { status: 200 }))

    await search('年假')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      query: '年假',
      top_k: 5,
      filters: undefined,
    })
  })

  it('提交反馈的请求体与后端契约一致', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(okEnvelope({}), { status: 200 }))

    await submitFeedback('qry-1', 'useful', '命中很准')

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/v1/feedback')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      query_id: 'qry-1',
      verdict: 'useful',
      comment: '命中很准',
    })
  })

  it('成功时把 details 视为空(不因多余字段报错)', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'OK',
          message: 'ok',
          data: { id: 'doc-1' },
          details: null,
          trace_id: 't-1',
          timestamp: '2026-10-31T08:00:00Z',
        }),
        { status: 200 },
      ),
    )

    const result = await request<{ id: string }>('/api/v1/documents/doc-1')

    expect(result.data.id).toBe('doc-1')
  })
})
