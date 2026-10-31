/**
 * 接缝 B:组件行为(issue #20)。
 *
 * 用 `@vue/test-utils` 挂载组件,断言**用户可见的行为**:
 * - 点击触发正确的请求并渲染结果;
 * - 拒答时显示拒答提示而**不渲染**引用列表;
 * - 提交过反馈时按钮回显选中态;
 * - 状态标签按文档状态给出正确呈现;
 * - 错误详情展示可复制的 trace_id。
 *
 * **不测**组件内部状态变量、不测 Element Plus 自身行为。
 *
 * 替身:组件测试不真打后端,用 `fetch` 拦截层注入固定的信封响应 —— 与
 * 后端"以假 LLM / 假向量库替身替换真实上游"是同一思路。
 *
 * Element Plus 用真实组件:组件行为测试要断言的正是"界面渲染出了什么",
 * 换成替身反而测的是替身的行为。真实组件在 jsdom 里可正常渲染。
 */
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import ErrorDetails from '@/components/ErrorDetails.vue'
import ChatView from '@/views/ChatView.vue'
import DocumentsView from '@/views/DocumentsView.vue'

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
function errorEnvelope(code: string, message: string, traceId = 'trace-err-0001'): string {
  return JSON.stringify({
    code,
    message,
    data: null,
    details: null,
    trace_id: traceId,
    timestamp: '2026-10-31T08:00:00Z',
  })
}

/** 一次问答的响应数据。 */
function chatAnswer(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    query_id: 'qry-1',
    question: '年假怎么算?',
    answer: '入职满一年可以申请年假。',
    refused: false,
    refusal_reason: null,
    top_k: 5,
    min_score: 0.35,
    filters: {},
    citations: [
      {
        index: 1,
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        file_name: '员工手册.md',
        title: '休假制度',
        page_number: 3,
        score: 0.87,
      },
    ],
    latency_ms: 12.5,
    generator: 'rule_based',
    token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    rewritten_question: null,
    expanded_questions: [],
    cache_hit: false,
    ...overrides,
  }
}

/** 一份文档档案。 */
function documentView(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'doc-1',
    file_name: '员工手册.md',
    file_type: 'md',
    size_bytes: 128,
    tenant: 'default',
    department: null,
    doc_type: null,
    version: 'v1',
    status: 'indexed',
    error_message: null,
    is_latest: true,
    page_count: 3,
    element_count: 12,
    chunk_count: 5,
    created_at: '2026-10-31T08:00:00Z',
    parsed_at: '2026-10-31T08:00:01Z',
    chunked_at: '2026-10-31T08:00:02Z',
    indexed_at: '2026-10-31T08:00:03Z',
    failed_at: null,
    superseded_at: null,
    deleted_at: null,
    ...overrides,
  }
}

/** 挂载组件:Pinia + TanStack Query 各给一个干净实例。 */
function mountView(component: unknown, options: { props?: Record<string, unknown> } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return mount(component as never, {
    attachTo: document.body,
    props: options.props as never,
    global: {
      plugins: [pinia, [VueQueryPlugin, { queryClient }], ElementPlus],
    },
  })
}

describe('组件行为', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  describe('问答页', () => {
    it('有据回答渲染正文与引用列表', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          return new Response(okEnvelope(chatAnswer()), { status: 200 })
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="chat-answer"]').exists()).toBe(true)
      })

      // 回答正文已渲染
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="chat-answer"]').text()).toContain('入职满一年')
      })
      // 引用列表已渲染,且带文件名与页码
      const citations = wrapper.find('[data-testid="chat-citations"]')
      expect(citations.exists()).toBe(true)
      expect(citations.text()).toContain('员工手册.md')
      expect(citations.text()).toContain('第 3 页')
    })

    it('拒答时显示拒答提示且不渲染引用列表', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          return new Response(
            okEnvelope(
              chatAnswer({
                query_id: 'qry-2',
                question: '量子计算怎么学?',
                answer: '根据当前资料无法确认。',
                refused: true,
                refusal_reason: 'no_hits',
                citations: [],
              }),
            ),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('量子计算怎么学?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="chat-refusal"]').exists()).toBe(true)
      })

      // 拒答提示带原因说明
      expect(wrapper.find('[data-testid="chat-refusal"]').text()).toContain('未检索到相关资料')
      // 引用列表不渲染(空引用区不占位)
      expect(wrapper.find('[data-testid="chat-citations"]').exists()).toBe(false)
    })

    it('提交过反馈时按钮回显选中态', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          return new Response(okEnvelope(chatAnswer({ query_id: 'qry-3' })), { status: 200 })
        }
        if (url.includes('/api/v1/feedback')) {
          return new Response(
            okEnvelope({
              total: 1,
              limit: 20,
              items: [
                {
                  feedback_id: 'fb-1',
                  query_id: 'qry-3',
                  trace_id: 't-1',
                  verdict: 'useful',
                  comment: null,
                  created_at: '2026-10-31T08:00:00Z',
                },
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope(null), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="feedback-state"]').exists()).toBe(true)
      })

      expect(wrapper.find('[data-testid="feedback-state"]').text()).toContain('有用')
    })

    it('提问失败时展示错误码与 trace_id', async () => {
      vi.mocked(fetch).mockImplementation(async () => {
        return new Response(
          errorEnvelope('UPSTREAM_TIMEOUT', '上游模型服务超时', 'trace-abc-123'),
          { status: 504 },
        )
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="error-code"]').exists()).toBe(true)
      })

      expect(wrapper.find('[data-testid="error-code"]').text()).toBe('UPSTREAM_TIMEOUT')
      expect(wrapper.find('[data-testid="error-trace-id"]').text()).toBe('trace-abc-123')
    })
  })

  describe('知识库文档页', () => {
    it('文档列表渲染文档名与状态标签', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/documents')) {
          return new Response(
            okEnvelope({
              total: 1,
              limit: 20,
              offset: 0,
              items: [documentView()],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope(null), { status: 200 })
      })

      const wrapper = mountView(DocumentsView)
      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('员工手册.md')
      })

      // 状态标签按状态给出正确呈现(已入库)
      expect(wrapper.text()).toContain('已入库')
      // 租户列也渲染
      expect(wrapper.text()).toContain('default')
    })

    it('失败状态的文档显示失败状态标签', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/documents')) {
          return new Response(
            okEnvelope({
              total: 1,
              limit: 20,
              offset: 0,
              items: [
                documentView({
                  id: 'doc-2',
                  file_name: '坏文件.txt',
                  file_type: 'txt',
                  status: 'failed',
                  error_message: '解析失败:文件编码不支持',
                  chunk_count: null,
                }),
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope(null), { status: 200 })
      })

      const wrapper = mountView(DocumentsView)
      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('坏文件.txt')
      })

      // 失败状态标签
      expect(wrapper.text()).toContain('失败')
    })
  })

  describe('错误详情区', () => {
    it('展示错误码、消息与可复制的 trace_id', () => {
      const error = new ApiError({
        code: 'UPSTREAM_TIMEOUT',
        message: '上游模型服务超时',
        details: { attempt: 3 },
        traceId: 'trace-abc-123',
      })

      const wrapper = mountView(ErrorDetails, { props: { error } })

      expect(wrapper.find('[data-testid="error-code"]').text()).toBe('UPSTREAM_TIMEOUT')
      expect(wrapper.text()).toContain('上游模型服务超时')
      expect(wrapper.find('[data-testid="error-trace-id"]').text()).toBe('trace-abc-123')
      // 详情可展开查看
      expect(wrapper.find('[data-testid="error-details"]').text()).toContain('attempt')
    })
  })
})
