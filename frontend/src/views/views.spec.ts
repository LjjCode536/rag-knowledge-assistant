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

/**
 * 等打字机动画把回答正文逐字显现完,再取角标。
 *
 * 回答以逐字显现的方式呈现(时长与后端真实耗时一致),组件测试里不能
 * 假设正文一出现就是完整的。
 */
async function waitForBadge(
  wrapper: Awaited<ReturnType<typeof mountView>>,
  selector: string,
) {
  let badge = wrapper.find(selector)
  await vi.waitFor(() => {
    badge = wrapper.find(selector)
    expect(badge.exists()).toBe(true)
  })
  return badge
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

    it('回答正文的编号角标可点击定位到对应引用', async () => {
      const scrolled: string[] = []
      // jsdom 没有 scrollIntoView:用替身记录"滚到了哪个元素"
      const originalScroll = Element.prototype.scrollIntoView
      Element.prototype.scrollIntoView = function scrollIntoView(this: Element) {
        scrolled.push(this.id)
      }
      try {
        vi.mocked(fetch).mockImplementation(async (input) => {
          const url = String(input)
          if (url.includes('/api/v1/chat')) {
            return new Response(
              okEnvelope(
                chatAnswer({
                  query_id: 'qry-badge',
                  answer: '入职满一年可以申请年假[1]。',
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
                }),
              ),
              { status: 200 },
            )
          }
          return new Response(
            okEnvelope({ total: 0, limit: 20, items: [] }),
            { status: 200 },
          )
        })

        const wrapper = mountView(ChatView)
        await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
        await wrapper.find('[data-testid="chat-submit"]').trigger('click')

        // 正文里的编号被渲染成可点击角标(不再是纯文本 "[1]")
        const badge = await waitForBadge(wrapper, '[data-testid="citation-badge-1"]')
        expect(badge.text()).toBe('1')

        // 点击角标 → 定位到下方对应的引用条目(真的滚过去了)
        await badge.trigger('click')
        await vi.waitFor(() => {
          const citation = wrapper.find('[data-testid="citation-1"]')
          expect(citation.exists()).toBe(true)
          expect(citation.attributes('data-highlighted')).toBe('true')
        })
        expect(scrolled).toContain('citation-1')
      } finally {
        Element.prototype.scrollIntoView = originalScroll
      }
    })

    it('每条引用可展开查看切片原文', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          return new Response(okEnvelope(chatAnswer({ query_id: 'qry-expand' })), {
            status: 200,
          })
        }
        if (url.includes('/api/v1/documents/doc-1/chunks')) {
          return new Response(
            okEnvelope({
              document_id: 'doc-1',
              status: 'indexed',
              chunk_count: 1,
              warnings: [],
              chunks: [
                {
                  chunk_id: 'chunk-1',
                  document_id: 'doc-1',
                  index: 0,
                  content: '年假:入职满一年后可申请,每年 5 天。',
                  tenant: 'default',
                  department: null,
                  doc_type: null,
                  version: 'v1',
                  page_number: 3,
                  title: '休假制度',
                  element_index_start: 0,
                  element_index_end: 2,
                },
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')

      // 展开前不显示切片原文
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="chat-citations"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="citation-text-1"]').exists()).toBe(false)

      // 展开后显示切片原文(用户能亲眼核实答案不是编的)
      await wrapper.find('[data-testid="citation-expand-1"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="citation-text-1"]').text()).toContain(
          '入职满一年后可申请',
        )
      })
    })

    it('回答以逐字显现呈现且总时长与后端真实耗时一致', async () => {
      vi.useFakeTimers()
      try {
        vi.mocked(fetch).mockImplementation(async (input) => {
          const url = String(input)
          if (url.includes('/api/v1/chat')) {
            return new Response(
              okEnvelope(
                chatAnswer({
                  query_id: 'qry-typing',
                  answer: '入职满一年可以申请年假。',
                  latency_ms: 1000,
                }),
              ),
              { status: 200 },
            )
          }
          return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
        })

        const wrapper = mountView(ChatView)
        await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
        await wrapper.find('[data-testid="chat-submit"]').trigger('click')

        // 等待响应落地(打字机开始)
        await vi.waitFor(() => {
          expect(wrapper.find('[data-testid="chat-answer"]').exists()).toBe(true)
        })

        // 动画尚未走完:正文还没显现完
        await vi.advanceTimersByTimeAsync(500)
        const midway = wrapper.find('[data-testid="chat-answer"]').text()
        expect(midway.length).toBeGreaterThan(0)
        expect(midway.length).toBeLessThan('入职满一年可以申请年假。'.length)

        // 走满后端真实耗时(1000 ms)后正文完整
        await vi.advanceTimersByTimeAsync(600)
        expect(wrapper.find('[data-testid="chat-answer"]').text()).toContain('入职满一年可以申请年假。')
      } finally {
        vi.useRealTimers()
      }
    })

    it('选「无用」时可附不超过 200 字的一句话说明', async () => {
      const bodies: string[] = []
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          return new Response(okEnvelope(chatAnswer({ query_id: 'qry-comment' })), {
            status: 200,
          })
        }
        if (url.includes('/api/v1/feedback')) {
          if (init?.body) {
            bodies.push(String(init.body))
          }
          return new Response(okEnvelope(null), { status: 200 })
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="feedback-useless"]').exists()).toBe(true)
      })

      // 附一句话说明后提交「无用」
      await wrapper.find('[data-testid="feedback-comment"]').setValue('回答没提到申请流程')
      await wrapper.find('[data-testid="feedback-useless"]').trigger('click')

      await vi.waitFor(() => {
        expect(bodies.length).toBe(1)
      })
      const submitted = JSON.parse(bodies[0]) as {
        query_id: string
        verdict: string
        comment: string | null
      }
      expect(submitted.query_id).toBe('qry-comment')
      expect(submitted.verdict).toBe('useless')
      expect(submitted.comment).toBe('回答没提到申请流程')
    })

    it('可设置过滤维度并展示后端回传的实际生效取值', async () => {
      const bodies: string[] = []
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          if (init?.body) {
            bodies.push(String(init.body))
          }
          return new Response(
            okEnvelope(
              chatAnswer({
                query_id: 'qry-filters',
                // 后端回传的是**实际生效**的口径(与界面发出的可能不同)
                top_k: 3,
                filters: { tenant: 'acme', department: '研发部' },
              }),
            ),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-filter-department"]').setValue('研发部')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')

      await vi.waitFor(() => {
        expect(bodies.length).toBe(1)
      })
      const submitted = JSON.parse(bodies[0]) as {
        top_k: number
        filters: Record<string, string>
      }
      expect(submitted.filters.department).toBe('研发部')

      // 界面展示后端回传的实际生效取值(不是界面自己发的那份)
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="chat-effective-params"]').exists()).toBe(true)
      })
      const effective = wrapper.find('[data-testid="chat-effective-params"]').text()
      expect(effective).toContain('3')
      expect(effective).toContain('研发部')
    })

    it('侧栏列出最近的提问,点开可查看该次问答的完整结果', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        // 单条查询日志(回看历史)与列表是两种形状,按路径分开应答
        if (url.includes('/api/v1/query-logs/')) {
          return new Response(
            okEnvelope({
              query_id: 'qry-old',
              trace_id: 't-old',
              question: '报销流程是什么?',
              answer: '根据知识库资料,差旅报销需要提交发票、行程单和审批记录。',
              refused: false,
              refusal_reason: null,
              hits: [
                {
                  rank: 1,
                  chunk_id: 'chunk-9',
                  document_id: 'doc-9',
                  title: '差旅制度',
                  page_number: 5,
                  score: 0.81,
                  used: true,
                },
              ],
              filters: { tenant: 'acme' },
              top_k: 5,
              min_score: 0.35,
              latency_ms: 88,
              generator: 'rule_based',
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
              rewritten_question: null,
              expanded_questions: [],
              created_at: '2026-10-31T09:00:00Z',
            }),
            { status: 200 },
          )
        }
        if (url.includes('/api/v1/query-logs')) {
          return new Response(
            okEnvelope({
              total: 2,
              limit: 20,
              items: [
                {
                  query_id: 'qry-old',
                  trace_id: 't-old',
                  question: '报销流程是什么?',
                  answer: '根据知识库资料,差旅报销需要提交发票、行程单和审批记录。',
                  refused: false,
                  refusal_reason: null,
                  hits: [
                    {
                      rank: 1,
                      chunk_id: 'chunk-9',
                      document_id: 'doc-9',
                      title: '差旅制度',
                      page_number: 5,
                      score: 0.81,
                      used: true,
                    },
                  ],
                  filters: { tenant: 'acme' },
                  top_k: 5,
                  min_score: 0.35,
                  latency_ms: 88,
                  generator: 'rule_based',
                  token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
                  rewritten_question: null,
                  expanded_questions: [],
                  created_at: '2026-10-31T09:00:00Z',
                },
                {
                  query_id: 'qry-old2',
                  trace_id: 't-old2',
                  question: '量子计算怎么学?',
                  answer: '根据当前资料无法确认。',
                  refused: true,
                  refusal_reason: 'no_hits',
                  hits: [],
                  filters: {},
                  top_k: 5,
                  min_score: 0.35,
                  latency_ms: 12,
                  generator: 'rule_based',
                  token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
                  rewritten_question: null,
                  expanded_questions: [],
                  created_at: '2026-10-31T08:00:00Z',
                },
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)

      // 侧栏列出最近的提问
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="recent-questions"]').text()).toContain(
          '报销流程是什么?',
        )
      })
      const sidebar = wrapper.find('[data-testid="recent-questions"]')
      expect(sidebar.text()).toContain('报销流程是什么?')

      // 点开 → 看到该次问答的完整结果(回答 + 命中)
      await wrapper.find('[data-testid="recent-question-qry-old"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="chat-answer"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="chat-answer"]').text()).toContain('差旅报销需要提交发票')
      // 该次问答的命中也看得到
      expect(wrapper.find('[data-testid="chat-hits"]').text()).toContain('差旅制度')
    })

    it('打开已有问答时回显既有反馈的判定与说明', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/chat')) {
          return new Response(okEnvelope(chatAnswer({ query_id: 'qry-existing' })), {
            status: 200,
          })
        }
        if (url.includes('/api/v1/feedback')) {
          return new Response(
            okEnvelope({
              total: 1,
              limit: 20,
              items: [
                {
                  feedback_id: 'fb-9',
                  query_id: 'qry-existing',
                  trace_id: 't-9',
                  verdict: 'useless',
                  comment: '答非所问',
                  created_at: '2026-10-31T08:00:00Z',
                },
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await wrapper.find('[data-testid="chat-question"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="chat-submit"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="feedback-state"]').exists()).toBe(true)
      })
      // 既有判定与既有说明都回显
      expect(wrapper.find('[data-testid="feedback-state"]').text()).toContain('无用')
      expect(wrapper.find('[data-testid="feedback-state"]').text()).toContain('答非所问')
      // 输入框预填既有说明(用户改写再提交即覆盖)
      expect(
        (wrapper.find('[data-testid="feedback-comment"]').element as HTMLInputElement).value,
      ).toBe('答非所问')
    })

    it('回看历史提问时,引用的文档名按文档档案补上', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/query-logs/qry-hist')) {
          return new Response(
            okEnvelope({
              query_id: 'qry-hist',
              trace_id: 't-hist',
              question: '报销流程是什么?',
              answer: '差旅报销需要提交发票、行程单和审批记录。',
              refused: false,
              refusal_reason: null,
              hits: [
                {
                  rank: 1,
                  chunk_id: 'chunk-9',
                  document_id: 'doc-9',
                  title: '差旅制度',
                  page_number: 5,
                  score: 0.81,
                  used: true,
                },
              ],
              filters: {},
              top_k: 5,
              min_score: 0.35,
              latency_ms: 88,
              generator: 'rule_based',
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
              rewritten_question: null,
              expanded_questions: [],
              created_at: '2026-10-31T09:00:00Z',
            }),
            { status: 200 },
          )
        }
        if (url.includes('/api/v1/query-logs')) {
          return new Response(
            okEnvelope({
              total: 1,
              limit: 20,
              items: [
                {
                  query_id: 'qry-hist',
                  trace_id: 't-hist',
                  question: '报销流程是什么?',
                  answer: '差旅报销需要提交发票、行程单和审批记录。',
                  refused: false,
                  refusal_reason: null,
                  hits: [],
                  filters: {},
                  top_k: 5,
                  min_score: 0.35,
                  latency_ms: 88,
                  generator: 'rule_based',
                  token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
                  rewritten_question: null,
                  expanded_questions: [],
                  created_at: '2026-10-31T09:00:00Z',
                },
              ],
            }),
            { status: 200 },
          )
        }
        if (url.includes('/api/v1/documents/doc-9')) {
          return new Response(
            okEnvelope({
              id: 'doc-9',
              file_name: '差旅报销制度.md',
              file_type: 'md',
              size_bytes: 100,
              tenant: 'acme',
              department: null,
              doc_type: null,
              version: 'v1',
              status: 'indexed',
              error_message: null,
              is_latest: true,
              page_count: 8,
              element_count: 20,
              chunk_count: 4,
              created_at: '2026-10-31T08:00:00Z',
              parsed_at: null,
              chunked_at: null,
              indexed_at: null,
              failed_at: null,
              superseded_at: null,
              deleted_at: null,
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(ChatView)
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="recent-questions"]').text()).toContain('报销流程是什么?')
      })
      await wrapper.find('[data-testid="recent-question-qry-hist"]').trigger('click')

      // 查询日志的命中不带文档名 → 界面按文档档案补上
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="citation-1"]').text()).toContain('差旅报销制度.md')
      })
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
