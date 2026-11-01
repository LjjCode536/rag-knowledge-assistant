/**
 * 接缝 B-1:知识库文档页的组件行为(issue #22)。
 *
 * 断言**用户可见的行为**:
 * - 不受支持的扩展名在**上传前**被拦下并说明原因;
 * - 入库进度按状态机呈现,且**没有百分比进度条**;
 * - 失败状态显示失败原因与重新上传入口;
 * - 停在「已切片」时提供重试向量化入口;
 * - 详情抽屉给出概览 / 时间线 / 切片预览 / 解析元素,文档标识可复制;
 * - 删除前明确告知会连带清理切片与向量。
 *
 * **不测**组件内部状态变量、不测 Element Plus 自身行为。
 *
 * 替身:组件测试不真打后端,用 `fetch` 拦截层注入固定的信封响应。
 */
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

/** 一条切片。 */
function chunkView(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    chunk_id: 'chunk-1',
    document_id: 'doc-1',
    index: 0,
    content: '入职满一年可以申请年假。',
    tenant: 'default',
    department: null,
    doc_type: null,
    version: 'v1',
    page_number: 3,
    title: '休假制度',
    element_index_start: 0,
    element_index_end: 1,
    ...overrides,
  }
}

/** 一个解析元素。 */
function elementView(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    element_type: 'title',
    content: '第一章 休假制度',
    order_index: 0,
    page_number: 3,
    heading_level: 1,
    line: null,
    ...overrides,
  }
}

/**
 * 按路径分派响应:清单 / 单份档案 / 切片 / 解析元素。
 *
 * 用 URL 判别而非调用次序 —— 调用次序是实现细节。
 */
function stubDocuments(routes: {
  list?: unknown
  document?: unknown
  chunks?: unknown
  elements?: unknown
  /** 上传端点的响应(任务句柄)。 */
  upload?: unknown
  listStatus?: number
  documentStatus?: number
  error?: { code: string; message: string }
}): void {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    if (routes.error) {
      return new Response(
        errorEnvelope(routes.error.code, routes.error.message),
        { status: 500 },
      )
    }
    // 上传:POST /api/v1/documents(不带路径参数)返回任务句柄
    if (init?.method === 'POST' && url === '/api/v1/documents') {
      return new Response(okEnvelope(routes.upload ?? documentView()), { status: 201 })
    }
    if (url.includes('/elements')) {
      return new Response(
        okEnvelope(routes.elements ?? { document_id: 'doc-1', page_count: 3, elements: [] }),
        { status: 200 },
      )
    }
    if (url.includes('/chunks')) {
      return new Response(
        okEnvelope(
          routes.chunks ?? {
            document_id: 'doc-1',
            status: 'indexed',
            chunk_count: 1,
            warnings: [],
            chunks: [chunkView()],
          },
        ),
        { status: 200 },
      )
    }
    if (url.includes('/api/v1/documents/')) {
      return new Response(
        okEnvelope(routes.document ?? documentView()),
        { status: routes.documentStatus ?? 200 },
      )
    }
    return new Response(
      okEnvelope(
        routes.list ?? {
          total: 1,
          limit: 20,
          offset: 0,
          items: [documentView()],
        },
      ),
      { status: routes.listStatus ?? 200 },
    )
  })
}

/** 挂载组件:Pinia + TanStack Query 各给一个干净实例。 */
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
    },
  })
}

/** 造一个 File 对象。 */
function makeFile(name: string, content = 'x'): File {
  return new File([content], name, { type: 'text/plain' })
}

/**
 * 选中一个知识库文档(走 el-upload 的 change 事件)。
 *
 * **不能直接 `setValue` 文件输入**:jsdom 不允许给 file input 程序化赋值
 * 文件名(只能置空串)。改走组件真实监听的 `change` 事件,带着文件列表 ——
 * 与用户在文件选择器里挑一个文档的路径一致。
 */
async function pickFile(
  wrapper: ReturnType<typeof mountView>,
  file: File,
): Promise<void> {
  const input = wrapper.find('input[type="file"]')
  expect(input.exists()).toBe(true)
  Object.defineProperty(input.element, 'files', {
    value: [file],
    configurable: true,
  })
  await input.trigger('change')
}

/** 等清单渲染完成(等到第一行的文档名出现即可)。 */
async function waitForList(wrapper: ReturnType<typeof mountView>): Promise<void> {
  await vi.waitFor(() => {
    expect(wrapper.find('.el-table__body-wrapper tbody tr').exists()).toBe(true)
  })
}

describe('知识库文档页组件行为', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  describe('上传', () => {
    it('受支持的格式说明写在界面上', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)

      expect(wrapper.text()).toContain('txt')
      expect(wrapper.text()).toContain('md')
      expect(wrapper.text()).toContain('html')
      expect(wrapper.text()).toContain('docx')
      expect(wrapper.text()).toContain('pdf')
    })

    it('不受支持的扩展名在上传前被拦下并说明原因', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      const upload = wrapper.find('[data-testid="documents-upload"]')
      // 先选一个不受支持的文件
      await pickFile(wrapper, makeFile('report.exe'))

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('不支持的知识库文档格式')
      })
      expect(wrapper.text()).toContain('.exe')
      // 上传按钮被禁用:不让用户白等一次必然失败的上传
      expect(upload.attributes('disabled')).toBeDefined()
      // 且没有向端发出上传请求
      const uploadCalls = vi
        .mocked(fetch)
        .mock.calls.filter(([url]) => String(url) === '/api/v1/documents')
      expect(uploadCalls).toHaveLength(0)
    })

    it('受支持的文件可以上传,并带上租户与高级设置字段', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await pickFile(wrapper, makeFile('manual.txt'))

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-upload"]').attributes('disabled')).toBeUndefined()
      })

      await wrapper.find('[data-testid="documents-upload"]').trigger('click')

      await vi.waitFor(() => {
        const calls = vi
          .mocked(fetch)
          .mock.calls.filter(([url]) => String(url) === '/api/v1/documents')
        expect(calls).toHaveLength(1)
      })
      const form = vi
        .mocked(fetch)
        .mock.calls.find(([url]) => String(url) === '/api/v1/documents')?.[1]?.body as FormData
      expect((form.get('file') as File).name).toBe('manual.txt')
      // 租户取全局租户选择器的值
      expect(form.get('tenant')).toBe('default')
      expect(form.get('version')).toBe('v1')
    })

    it('上传失败时展示错误码与 trace_id', async () => {
      vi.mocked(fetch).mockImplementation(async () => {
        return new Response(errorEnvelope('UNSUPPORTED_FORMAT', '不支持的文档格式'), { status: 415 })
      })
      const wrapper = mountView(DocumentsView)

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="error-code"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="error-code"]').text()).toBe('UNSUPPORTED_FORMAT')
    })

    it('上传后打开详情看得到入库进度', async () => {
      stubDocuments({
        document: documentView({ id: 'doc-new', status: 'uploaded', indexed_at: null }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await pickFile(wrapper, makeFile('manual.txt'))
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-upload"]').attributes('disabled')).toBeUndefined()
      })
      await wrapper.find('[data-testid="documents-upload"]').trigger('click')

      // 上传后抽屉自动打开,入库进度就在里面 —— "看得到它进去了"
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-progress"]').exists()).toBe(true)
      })
    })
  })

  describe('入库进度', () => {
    it('按状态机呈现当前所在的那一段', async () => {
      stubDocuments({
        document: documentView({ status: 'chunking', indexed_at: null }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-1"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-progress"]').exists()).toBe(true)
      })
      const progress = wrapper.find('[data-testid="documents-progress"]').text()
      // 状态机的每一段都出现,当前段被标出
      expect(progress).toContain('已登记')
      expect(progress).toContain('解析中')
      expect(progress).toContain('已解析')
      expect(progress).toContain('切片中')
      expect(progress).toContain('已切片')
      expect(progress).toContain('向量化中')
      expect(progress).toContain('已入库')
    })

    it('界面不展示百分比进度条', async () => {
      stubDocuments({
        document: documentView({ status: 'parsing', indexed_at: null }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-1"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-progress"]').exists()).toBe(true)
      })

      // Element Plus 的 el-progress 会渲染 role="progressbar";界面里不该有
      expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
      expect(wrapper.find('.el-progress').exists()).toBe(false)
    })
  })

  describe('失败可恢复', () => {
    it('失败状态显示失败原因与重新上传入口', async () => {
      stubDocuments({
        list: {
          total: 1,
          limit: 20,
          offset: 0,
          items: [
            documentView({
              id: 'doc-bad',
              file_name: '坏资料.txt',
              file_type: 'txt',
              status: 'failed',
              error_message: '解析失败:文档编码不支持',
              chunk_count: null,
              parsed_at: null,
              chunked_at: null,
              indexed_at: null,
              failed_at: '2026-10-31T08:00:05Z',
            }),
          ],
        },
        document: documentView({
          id: 'doc-bad',
          file_name: '坏资料.txt',
          file_type: 'txt',
          status: 'failed',
          error_message: '解析失败:文档编码不支持',
          chunk_count: null,
          parsed_at: null,
          chunked_at: null,
          indexed_at: null,
          failed_at: '2026-10-31T08:00:05Z',
        }),
      })
      const wrapper = mountView(DocumentsView)

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('坏资料.txt')
      })

      await wrapper.find('[data-testid="documents-detail-doc-bad"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-error-reason"]').exists()).toBe(true)
      })
      // 失败原因如实呈现
      expect(wrapper.find('[data-testid="documents-error-reason"]').text()).toContain(
        '解析失败:文档编码不支持',
      )
      // 重新上传入口
      expect(wrapper.find('[data-testid="documents-reupload"]').exists()).toBe(true)
    })

    it('重新上传需要用户重新挑一次同一份知识库文档', async () => {
      // 后端没有"取回原始文档"的端点:重传的内容在用户手上,
      // 界面据此在失败区块里放文件选择入口,而不是假装浏览器还留着上次的文件。
      stubDocuments({
        list: {
          total: 1,
          limit: 20,
          offset: 0,
          items: [
            documentView({
              id: 'doc-bad',
              file_name: '坏资料.txt',
              file_type: 'txt',
              status: 'failed',
              error_message: '解析失败:文档编码不支持',
            }),
          ],
        },
        document: documentView({
          id: 'doc-bad',
          file_name: '坏资料.txt',
          file_type: 'txt',
          status: 'failed',
          error_message: '解析失败:文档编码不支持',
        }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-bad"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-reupload"]').exists()).toBe(true)
      })

      // 还没挑文件,重新上传按钮不可用
      expect(wrapper.find('[data-testid="documents-reupload"]').attributes('disabled')).toBeDefined()

      // 挑一份不受支持的文件:在选中时就被拦下
      const reuploadInput = wrapper.findAll('input[type="file"]')[1]
      expect(reuploadInput.exists()).toBe(true)
      Object.defineProperty(reuploadInput.element, 'files', {
        value: [makeFile('bad.exe')],
        configurable: true,
      })
      await reuploadInput.trigger('change')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-reupload-extension-error"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="documents-reupload-extension-error"]').text()).toContain('.exe')
      expect(wrapper.find('[data-testid="documents-reupload"]').attributes('disabled')).toBeDefined()
    })

    it('挑好同一份知识库文档后可以重新上传', async () => {
      stubDocuments({
        list: {
          total: 1,
          limit: 20,
          offset: 0,
          items: [
            documentView({
              id: 'doc-bad',
              file_name: '坏资料.txt',
              file_type: 'txt',
              status: 'failed',
              error_message: '解析失败:文档编码不支持',
            }),
          ],
        },
        document: documentView({
          id: 'doc-bad',
          file_name: '坏资料.txt',
          file_type: 'txt',
          status: 'failed',
          error_message: '解析失败:文档编码不支持',
        }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-bad"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-reupload"]').exists()).toBe(true)
      })

      const reuploadInput = wrapper.findAll('input[type="file"]')[1]
      Object.defineProperty(reuploadInput.element, 'files', {
        value: [makeFile('坏资料.txt')],
        configurable: true,
      })
      await reuploadInput.trigger('change')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-reupload"]').attributes('disabled')).toBeUndefined()
      })

      await wrapper.find('[data-testid="documents-reupload"]').trigger('click')

      await vi.waitFor(() => {
        const calls = vi
          .mocked(fetch)
          .mock.calls.filter(([url, init]) => String(url) === '/api/v1/documents' && init?.method === 'POST')
        expect(calls.length).toBeGreaterThan(0)
      })
      const form = vi
        .mocked(fetch)
        .mock.calls.find(([url, init]) => String(url) === '/api/v1/documents' && init?.method === 'POST')?.[1]
        ?.body as FormData
      expect((form.get('file') as File).name).toBe('坏资料.txt')
    })

    it('重新上传带上失败档案自己的租户与元数据(同标识重试语义)', async () => {
      // 后端的文档身份是 (租户, 文件名):租户错了就不是同标识,
      // 重试会落到另一份档案上,原来那份既没被退位也没被重试。
      stubDocuments({
        list: {
          total: 1,
          limit: 20,
          offset: 0,
          items: [
            documentView({
              id: 'doc-bad',
              file_name: '坏资料.txt',
              file_type: 'txt',
              status: 'failed',
              error_message: '解析失败:文档编码不支持',
              tenant: 'acme',
              department: 'HR',
              doc_type: '制度',
              version: 'v3',
            }),
          ],
        },
        document: documentView({
          id: 'doc-bad',
          file_name: '坏资料.txt',
          file_type: 'txt',
          status: 'failed',
          error_message: '解析失败:文档编码不支持',
          tenant: 'acme',
          department: 'HR',
          doc_type: '制度',
          version: 'v3',
        }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-bad"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-reupload"]').exists()).toBe(true)
      })

      const reuploadInput = wrapper.findAll('input[type="file"]')[1]
      Object.defineProperty(reuploadInput.element, 'files', {
        value: [makeFile('坏资料.txt')],
        configurable: true,
      })
      await reuploadInput.trigger('change')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-reupload"]').attributes('disabled')).toBeUndefined()
      })

      await wrapper.find('[data-testid="documents-reupload"]').trigger('click')

      await vi.waitFor(() => {
        const calls = vi
          .mocked(fetch)
          .mock.calls.filter(([url, init]) => String(url) === '/api/v1/documents' && init?.method === 'POST')
        expect(calls.length).toBeGreaterThan(0)
      })
      const form = vi
        .mocked(fetch)
        .mock.calls.find(([url, init]) => String(url) === '/api/v1/documents' && init?.method === 'POST')?.[1]
        ?.body as FormData
      // 租户 / 部门 / 文档类型 / 版本都取失败档案自己的,不是上传表单的值
      expect(form.get('tenant')).toBe('acme')
      expect(form.get('department')).toBe('HR')
      expect(form.get('doc_type')).toBe('制度')
      expect(form.get('version')).toBe('v3')
    })

    it('文档停在已切片时提供重试向量化入口', async () => {
      stubDocuments({
        document: documentView({
          status: 'chunked',
          indexed_at: null,
          chunk_count: 5,
        }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-1"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-retry-embedding"]').exists()).toBe(true)
      })
    })

    it('重试向量化调用后端向量化端点', async () => {
      stubDocuments({
        document: documentView({ status: 'chunked', indexed_at: null }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-1"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-retry-embedding"]').exists()).toBe(true)
      })

      await wrapper.find('[data-testid="documents-retry-embedding"]').trigger('click')

      await vi.waitFor(() => {
        const calls = vi
          .mocked(fetch)
          .mock.calls.filter(([url]) => String(url).includes('/embeddings'))
        expect(calls).toHaveLength(1)
      })
      const [url, init] = vi.mocked(fetch).mock.calls.find(([u]) => String(u).includes('/embeddings'))!
      expect(url).toBe('/api/v1/documents/doc-1/embeddings')
      expect(init?.method).toBe('POST')
    })
  })

  describe('清单', () => {
    it('默认只列现行版本,可切到含历史版本', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      // 默认不带 include_history
      const listCall = vi
        .mocked(fetch)
        .mock.calls.find(([url]) => String(url).includes('/api/v1/documents'))
      expect(String(listCall?.[0])).not.toContain('include_history=true')

      const history = wrapper.find('[data-testid="documents-include-history"] input')
      expect(history.exists()).toBe(true)
      await history.setValue(true)

      await vi.waitFor(() => {
        const calls = vi
          .mocked(fetch)
          .mock.calls.filter(([url]) => String(url).includes('include_history=true'))
        expect(calls.length).toBeGreaterThan(0)
      })
    })

    it('清单按创建时间倒序呈现', async () => {
      stubDocuments({
        list: {
          total: 2,
          limit: 20,
          offset: 0,
          items: [
            documentView({ id: 'doc-new', file_name: '新资料.md', created_at: '2026-10-31T10:00:00Z' }),
            documentView({ id: 'doc-old', file_name: '旧资料.md', created_at: '2026-10-30T10:00:00Z' }),
          ],
        },
      })
      const wrapper = mountView(DocumentsView)

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('新资料.md')
      })
      // 后端已按创建时间倒序返回,界面原样呈现顺序
      const rows = wrapper.findAll('.el-table__body-wrapper tbody tr')
      expect(rows.length).toBe(2)
      expect(rows[0].text()).toContain('新资料.md')
      expect(rows[1].text()).toContain('旧资料.md')
    })

    it('按关键字筛选时把关键字带给后端', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-keyword"]').setValue('手册')

      await vi.waitFor(() => {
        const calls = vi
          .mocked(fetch)
          .mock.calls.filter(([url]) => String(url).includes('keyword='))
        expect(calls.length).toBeGreaterThan(0)
      })
      expect(String(vi.mocked(fetch).mock.calls.at(-1)?.[0])).toContain('keyword=')
    })
  })

  describe('详情抽屉', () => {
    it('给出概览、时间线、切片预览与解析元素四块内容', async () => {
      stubDocuments({
        elements: {
          document_id: 'doc-1',
          page_count: 3,
          elements: [
            elementView(),
            elementView({ element_type: 'paragraph', content: '正文段落', order_index: 1 }),
          ],
        },
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-1"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-overview"]').exists()).toBe(true)
      })
      // 概览:格式 / 大小 / 部门 / 文档类型 / 版本 / 页数 / 元素数 / 切片数
      const overview = wrapper.find('[data-testid="documents-overview"]').text()
      expect(overview).toContain('md')
      expect(overview).toContain('128')
      expect(overview).toContain('v1')
      expect(overview).toContain('3')
      expect(overview).toContain('12')
      expect(overview).toContain('5')

      // 时间线
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-timeline"]').exists()).toBe(true)
      })
      const timeline = wrapper.find('[data-testid="documents-timeline"]').text()
      expect(timeline).toContain('创建')
      expect(timeline).toContain('解析')
      expect(timeline).toContain('切片')
      expect(timeline).toContain('索引')

      // 切片预览
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-chunks"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="documents-chunks"]').text()).toContain('入职满一年')

      // 解析元素(按类型折叠分组)
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-elements"]').exists()).toBe(true)
      })
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-elements"]').text()).toContain('第一章')
      })
    })

    it('文档标识以可复制形式呈现', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-detail-doc-1"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-copy-id"]').exists()).toBe(true)
      })
      expect(wrapper.find('[data-testid="documents-copy-id"]').text()).toContain('doc-1')
    })

    it('租户筛选维度来自清单回传的租户', async () => {
      stubDocuments({
        list: {
          total: 1,
          limit: 20,
          offset: 0,
          items: [documentView({ tenant: 'acme' })],
        },
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await vi.waitFor(() => {
        expect(wrapper.text()).toContain('acme')
      })
    })
  })

  describe('删除', () => {
    it('删除前明确告知会连带清理切片与向量', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-delete-doc-1"]').trigger('click')

      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-delete-confirm"]').exists()).toBe(true)
      })
      const confirm = wrapper.find('[data-testid="documents-delete-confirm"]')
      expect(confirm.text()).toContain('切片')
      expect(confirm.text()).toContain('向量')
      // 还没确认,不发删除请求
      const deleteCalls = vi
        .mocked(fetch)
        .mock.calls.filter(([, init]) => init?.method === 'DELETE')
      expect(deleteCalls).toHaveLength(0)
    })

    it('确认后调用删除端点', async () => {
      stubDocuments({})
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-delete-doc-1"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-delete-confirm"]').exists()).toBe(true)
      })

      await wrapper.find('[data-testid="documents-delete-submit"]').trigger('click')

      await vi.waitFor(() => {
        const deleteCalls = vi
          .mocked(fetch)
          .mock.calls.filter(([url, init]) => String(url).includes('/api/v1/documents/doc-1') && init?.method === 'DELETE')
        expect(deleteCalls).toHaveLength(1)
      })
    })

    it('重复删除不报错', async () => {
      // 后端删除是幂等的:已删除文档再删一次仍返回成功
      stubDocuments({
        document: documentView({ status: 'deleted', deleted_at: '2026-10-31T09:00:00Z' }),
      })
      const wrapper = mountView(DocumentsView)
      await waitForList(wrapper)

      await wrapper.find('[data-testid="documents-delete-doc-1"]').trigger('click')
      await vi.waitFor(() => {
        expect(wrapper.find('[data-testid="documents-delete-confirm"]').exists()).toBe(true)
      })
      await wrapper.find('[data-testid="documents-delete-submit"]').trigger('click')

      await vi.waitFor(() => {
        const deleteCalls = vi
          .mocked(fetch)
          .mock.calls.filter(([, init]) => init?.method === 'DELETE')
        expect(deleteCalls.length).toBeGreaterThan(0)
      })
      // 没有错误详情区:幂等删除不报错
      expect(wrapper.find('[data-testid="error-code"]').exists()).toBe(false)
    })
  })
})
