/**
 * 接缝 B:检索 / 运营统计 / 查询日志 / 反馈四个页面的组件行为(issue #23、#24)。
 *
 * 断言**用户可见的行为**:
 * - 统计页按区间与分桶粒度查询时**真的把参数传给后端**;
 * - 无样本时拒答率与平均延迟显示「无样本」而不是 0;
 * - 检索页展示部门 / 文档类型 / 版本,过滤维度随请求提交并反映到结果;
 * - 查询日志页展示生成器种类、命中数与 Token 用量,并标注最近 100 条上限;
 * - 反馈页展示差评候选的来源上下文与评论,CSV 导出不走统一信封拆解。
 *
 * **不测**组件内部状态变量、不测 Element Plus 自身行为。
 *
 * 替身:组件测试不真打后端,用 `fetch` 拦截层注入固定的响应。
 */
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus, { ElDatePicker } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import FeedbackView from '@/views/FeedbackView.vue'
import QueryLogsView from '@/views/QueryLogsView.vue'
import SearchView from '@/views/SearchView.vue'
import StatsView from '@/views/StatsView.vue'

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

/** 挂载组件:Pinia + TanStack Query 各给一个干净实例;图表用替身(canvas 不在 jsdom 里)。 */
function mountView(component: unknown) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return mount(component as never, {
    attachTo: document.body,
    global: {
      plugins: [pinia, [VueQueryPlugin, { queryClient }], ElementPlus],
      // 断言的是页面数据行为,不是 ECharts 的绘制行为
      stubs: { VChart: { template: '<div class="chart-stub" />' } },
    },
  })
}

/** 一问一答的查询日志。 */
function queryLog(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    query_id: 'qry-1',
    trace_id: 'trace-1',
    question: '年假怎么算?',
    answer: '入职满一年可以申请年假。',
    refused: false,
    refusal_reason: null,
    hits: [
      {
        rank: 1,
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        title: '休假制度',
        page_number: 3,
        score: 0.87,
        used: true,
      },
      {
        rank: 2,
        chunk_id: 'chunk-2',
        document_id: 'doc-1',
        title: '休假制度',
        page_number: 4,
        score: 0.52,
        used: false,
      },
    ],
    filters: { tenant: 'acme' },
    top_k: 5,
    min_score: 0.35,
    latency_ms: 88,
    generator: 'llm',
    token_usage: { prompt_tokens: 120, completion_tokens: 30, total_tokens: 150 },
    rewritten_question: '年假 计算 规则',
    expanded_questions: ['年假怎么休', '年假天数'],
    created_at: '2026-10-31T09:00:00Z',
    ...overrides,
  }
}

/** 一条差评候选(含来源上下文与评论)。 */
function candidate(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'candidate-1',
    question: '年假怎么算?',
    occurrences: 3,
    failure: '回答没用',
    first_seen: '2026-10-31T08:00:00Z',
    last_seen: '2026-10-31T09:00:00Z',
    comments: ['答非所问', '没有引用'],
    origins: [
      {
        query_id: 'qry-9',
        trace_id: 'trace-9',
        verdict: 'useless',
        refused: true,
        refusal_reason: 'low_score',
        top_documents: ['员工手册.md', '报销制度.md'],
        comment: '资料不对',
        created_at: '2026-10-31T09:00:00Z',
      },
    ],
    ...overrides,
  }
}

describe('检索与运营统计页组件行为', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  describe('运营统计页', () => {
    it('无样本时拒答率与平均延迟显示「无样本」而不是 0', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/stats/cost')) {
          return new Response(
            okEnvelope({
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
              cost: { prompt_cost: 0, completion_cost: 0, total_cost: 0, currency: 'CNY' },
              start: null,
              end: null,
            }),
            { status: 200 },
          )
        }
        return new Response(
          okEnvelope({
            document_count: 0,
            indexed_document_count: 0,
            chunk_count: 0,
            qa: {
              query_count: 0,
              refused_count: 0,
              refusal_rate: null,
              avg_latency_ms: null,
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
            },
            cost: { prompt_cost: 0, completion_cost: 0, total_cost: 0, currency: 'CNY' },
            buckets: [],
            start: null,
            end: null,
            bucket_kind: 'day',
          }),
          { status: 200 },
        )
      })

      const wrapper = mountView(StatsView)

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="stats-refusal-rate"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="stats-refusal-rate"]').text()).toBe('无样本')
      expect(wrapper.find('[data-testid="stats-avg-latency"]').text()).toBe('无样本')
    })

    it('切换分桶粒度后把实际取值为 hour 的参数传给后端', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/stats/cost')) {
          return new Response(
            okEnvelope({
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
              cost: { prompt_cost: 0, completion_cost: 0, total_cost: 0, currency: 'CNY' },
              start: null,
              end: null,
            }),
            { status: 200 },
          )
        }
        return new Response(
          okEnvelope({
            document_count: 1,
            indexed_document_count: 1,
            chunk_count: 2,
            qa: {
              query_count: 1,
              refused_count: 0,
              refusal_rate: 0,
              avg_latency_ms: 12,
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
            },
            cost: { prompt_cost: 0, completion_cost: 0, total_cost: 0, currency: 'CNY' },
            buckets: [],
            start: null,
            end: null,
            bucket_kind: 'hour',
          }),
          { status: 200 },
        )
      })

      const wrapper = mountView(StatsView)

      // 初次请求带上默认分桶粒度
      await vi.waitFor(() => {
        const statsCall = vi
          .mocked(fetch)
          .mock.calls.find(([url]) => String(url).includes('/api/v1/stats?'))
        expect(statsCall).toBeDefined()
        expect(new URL(String(statsCall?.[0]), 'http://localhost').searchParams.get('bucket')).toBe(
          'day',
        )
      })

      // 用户切到「按自然小时」
      await wrapper.find('input[value="hour"]').setValue(true)

      await vi.waitFor(() => {
        const statsCalls = vi
          .mocked(fetch)
          .mock.calls.map(([url]) => String(url))
          .filter((url) => url.includes('/api/v1/stats?'))
        const hourly = statsCalls.find(
          (url) => new URL(url, 'http://localhost').searchParams.get('bucket') === 'hour',
        )
        expect(hourly).toBeDefined()
      })
    })

    it('选择统计区间后把闭区间起点 / 开区间终点传给后端', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/stats/cost')) {
          return new Response(
            okEnvelope({
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
              cost: { prompt_cost: 0, completion_cost: 0, total_cost: 0, currency: 'CNY' },
              start: null,
              end: null,
            }),
            { status: 200 },
          )
        }
        return new Response(
          okEnvelope({
            document_count: 1,
            indexed_document_count: 1,
            chunk_count: 2,
            qa: {
              query_count: 0,
              refused_count: 0,
              refusal_rate: null,
              avg_latency_ms: null,
              token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
            },
            cost: { prompt_cost: 0, completion_cost: 0, total_cost: 0, currency: 'CNY' },
            buckets: [],
            start: null,
            end: null,
            bucket_kind: 'day',
          }),
          { status: 200 },
        )
      })

      const wrapper = mountView(StatsView)
      const start = new Date('2026-11-01T00:00:00Z')
      const end = new Date('2026-11-03T00:00:00Z')

      // 日期选择器的取值经 v-model 进入查询参数(终点不含)
      wrapper.findComponent(ElDatePicker).vm.$emit('update:modelValue', [start, end])

      await vi.waitFor(() => {
        const statsCalls = vi
          .mocked(fetch)
          .mock.calls.map(([url]) => String(url))
          .filter((url) => url.includes('/api/v1/stats?'))
        const withRange = statsCalls.find(
          (url) => new URL(url, 'http://localhost').searchParams.get('start') !== null,
        )
        expect(withRange).toBeDefined()
        const params = new URL(withRange!, 'http://localhost').searchParams
        expect(params.get('start')).toBe(start.toISOString())
        expect(params.get('end')).toBe(end.toISOString())
      })
    })
  })

  describe('检索页', () => {
    it('按过滤维度检索,提交参数并渲染部门 / 文档类型 / 版本列', async () => {
      const bodies: string[] = []
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const url = String(input)
        if (url.includes('/api/v1/search')) {
          if (init?.body) {
            bodies.push(String(init.body))
          }
          return new Response(
            okEnvelope({
              query: '年假怎么算?',
              top_k: 5,
              total: 1,
              // 后端回传实际生效的过滤口径
              filters: {
                tenant: 'acme',
                department: '研发部',
                doc_type: '制度',
                version: 'v2',
              },
              hits: [
                {
                  score: 0.87,
                  chunk: {
                    chunk_id: 'chunk-1',
                    document_id: 'doc-1',
                    index: 0,
                    content: '入职满一年可以申请年假。',
                    tenant: 'acme',
                    department: '研发部',
                    doc_type: '制度',
                    version: 'v2',
                    page_number: 3,
                    title: '休假制度',
                    element_index_start: 0,
                    element_index_end: 1,
                  },
                },
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope({ total: 0, limit: 20, items: [] }), { status: 200 })
      })

      const wrapper = mountView(SearchView)
      await wrapper.find('[data-testid="search-query"]').setValue('年假怎么算?')
      await wrapper.find('[data-testid="search-filter-department"]').setValue('研发部')
      await wrapper.find('[data-testid="search-filter-doc-type"]').setValue('制度')
      await wrapper.find('[data-testid="search-filter-version"]').setValue('v2')
      await wrapper.find('[data-testid="search-submit"]').trigger('click')

      await vi.waitFor(() => {
        expect(bodies.length).toBe(1)
      })
      const submitted = JSON.parse(bodies[0]) as {
        query: string
        filters: Record<string, string>
      }
      expect(submitted.query).toBe('年假怎么算?')
      expect(submitted.filters.department).toBe('研发部')
      expect(submitted.filters.doc_type).toBe('制度')
      expect(submitted.filters.version).toBe('v2')

      // 命中列表展示部门 / 文档类型 / 版本(切片元数据)
      await vi.waitFor(() => {
        expect(wrapper.find('.el-table__body-wrapper tbody tr').exists()).toBe(true)
      })
      const row = wrapper.find('.el-table__body-wrapper tbody tr').text()
      expect(row).toContain('研发部')
      expect(row).toContain('制度')
      expect(row).toContain('v2')
      // 生效过滤口径按规范术语展示
      expect(wrapper.text()).toContain('部门:研发部')
    })
  })
})

describe('查询日志与反馈页组件行为', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  describe('查询日志页', () => {
    it('展示生成器种类、命中数与 Token 用量,并标注最近一百条上限', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/query-logs')) {
          return new Response(
            okEnvelope({ total: 1, limit: 100, items: [queryLog()] }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope(null), { status: 200 })
      })

      const wrapper = mountView(QueryLogsView)

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="query-log-generator"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="query-log-generator"]').text()).toBe('LLM')
      expect(wrapper.find('[data-testid="query-log-hit-count"]').text()).toBe('2')
      expect(wrapper.find('.el-table__body-wrapper tbody tr').text()).toContain('150')
      // 分页上限如实标注
      expect(wrapper.text()).toContain('最近 100 条')
      expect(wrapper.text()).toContain('完整历史见后端 API')
    })

    it('展开一条查询日志可看到改写后的问题、扩展变体与命中是否进入证据', async () => {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/query-logs')) {
          return new Response(
            okEnvelope({ total: 1, limit: 100, items: [queryLog()] }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope(null), { status: 200 })
      })

      const wrapper = mountView(QueryLogsView)
      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('年假怎么算?')
      })

      await wrapper.find('.el-table__expand-icon').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('年假 计算 规则')
      })
      expect(wrapper.text()).toContain('年假怎么休; 年假天数')
      expect(wrapper.text()).toContain('进入证据')
      expect(wrapper.text()).toContain('未进入证据')
    })
  })

  describe('反馈页', () => {
    /** 按路径分派反馈列表 / 差评候选 / CSV 导出。 */
    function stubFeedback(options: { csv?: string } = {}): void {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/feedback/negative/candidates')) {
          if (url.includes('format=csv')) {
            return new Response(options.csv ?? '\ufeffid,question\ncandidate-1,年假怎么算?', {
              status: 200,
              headers: { 'Content-Type': 'text/csv; charset=utf-8' },
            })
          }
          return new Response(
            okEnvelope({ total: 1, limit: 100, items: [candidate()] }),
            { status: 200 },
          )
        }
        if (url.includes('/api/v1/feedback')) {
          return new Response(
            okEnvelope({
              total: 2,
              limit: 100,
              items: [
                {
                  feedback_id: 'fb-1',
                  query_id: 'qry-1',
                  trace_id: 'trace-1',
                  verdict: 'useful',
                  comment: '回答很准确',
                  created_at: '2026-10-31T09:00:00Z',
                },
                {
                  feedback_id: 'fb-2',
                  query_id: 'qry-2',
                  trace_id: 'trace-2',
                  verdict: 'useless',
                  comment: '答非所问',
                  created_at: '2026-10-31T09:30:00Z',
                },
              ],
            }),
            { status: 200 },
          )
        }
        return new Response(okEnvelope(null), { status: 200 })
      })
    }

    it('展示反馈判定与附言,并标注最近一百条上限', async () => {
      stubFeedback()
      const wrapper = mountView(FeedbackView)

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('回答很准确')
      })
      expect(wrapper.text()).toContain('有用')
      expect(wrapper.text()).toContain('无用')
      expect(wrapper.text()).toContain('答非所问')
      expect(wrapper.text()).toContain('最近 100 条')
      expect(wrapper.text()).toContain('完整历史见后端 API')
    })

    it('差评候选展示来源上下文与评论', async () => {
      stubFeedback()
      const wrapper = mountView(FeedbackView)

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('年假怎么算?')
      })

      // 候选行直接展示评论
      expect(wrapper.text()).toContain('答非所问')
      expect(wrapper.text()).toContain('没有引用')

      // 展开候选看来源上下文:问答、拒答原因、命中文档、评论
      await wrapper.find('.el-table__expand-icon').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="candidate-origin"]').exists()).toBe(true)
      })
      const origin = wrapper.find('[data-testid="candidate-origin"]').text()
      expect(origin).toContain('qry-9')
      expect(origin).toContain('拒答')
      expect(origin).toContain('资料相关度不足')
      expect(origin).toContain('员工手册.md')
      expect(origin).toContain('报销制度.md')
      expect(origin).toContain('资料不对')
    })

    it('CSV 导出按原始文本处理,不走统一响应信封拆解', async () => {
      const csv = '\ufeffid,question,occurrences\ncandidate-1,年假怎么算?,3'
      stubFeedback({ csv })
      const wrapper = mountView(FeedbackView)

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="feedback-export"]').exists()).toBe(true)
      })
      await wrapper.find('[data-testid="feedback-export"]').trigger('click')

      // 若误走信封拆解,裸 CSV 会解析失败并渲染错误详情
      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('保存导出的 CSV')
      })
      expect(wrapper.find('[data-testid="error-code"]').exists()).toBe(false)

      const csvCall = vi
        .mocked(fetch)
        .mock.calls.map(([url]) => String(url))
        .find((url) => url.includes('format=csv'))
      expect(csvCall).toBeDefined()
    })
  })
})
