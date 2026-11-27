/**
 * 接缝 B-2:知识库文档页的纯逻辑(issue #22)。
 *
 * 断言外部行为:扩展名是否受支持、状态机推进到哪一段、时间线如何排列、
 * 解析元素如何按类型分组、状态标签怎么呈现。
 *
 * **不测**组件内部状态变量,不测 Element Plus 自身行为。
 */
import { describe, expect, it } from 'vitest'

import {
  SUPPORTED_EXTENSIONS,
  buildTimeline,
  extensionOf,
  groupElementsByType,
  isSupportedExtension,
  progressSteps,
  statusLabel,
  statusTagType,
  type ProgressStep,
} from './documentLogic'

describe('知识库文档页纯逻辑', () => {
  describe('扩展名校验', () => {
    it('支持的扩展名通过校验', () => {
      for (const name of ['a.txt', 'b.md', 'c.html', 'd.docx', 'e.pdf']) {
        expect(isSupportedExtension(name)).toBe(true)
      }
    })

    it('markdown 与 htm 是别名,同样受支持', () => {
      expect(isSupportedExtension('a.markdown')).toBe(true)
      expect(isSupportedExtension('a.htm')).toBe(true)
    })

    it('扩展名大小写不敏感', () => {
      expect(isSupportedExtension('A.TXT')).toBe(true)
      expect(isSupportedExtension('A.Md')).toBe(true)
    })

    it('不受支持的扩展名被拦下', () => {
      for (const name of ['a.exe', 'b.pptx', 'c.xlsx', 'd.zip', 'e.json']) {
        expect(isSupportedExtension(name)).toBe(false)
      }
    })

    it('无扩展名不被拦下(由后端判定),但扩展名取空串', () => {
      expect(extensionOf('README')).toBe('')
      expect(isSupportedExtension('README')).toBe(false)
    })

    it('只取最后一段扩展名,不被点号误导', () => {
      expect(extensionOf('a.b.c.txt')).toBe('txt')
      expect(extensionOf('dir.d/file.md')).toBe('md')
    })

    it('受支持的扩展名清单含全部五种格式与两个别名', () => {
      expect(SUPPORTED_EXTENSIONS).toEqual(
        expect.arrayContaining(['txt', 'md', 'markdown', 'html', 'htm', 'docx', 'pdf']),
      )
    })
  })

  describe('入库进度', () => {
    it('进度按状态机逐段推进,当前段高亮', () => {
      const steps = progressSteps('parsing')

      expect(steps.map((step) => step.key)).toEqual([
        'uploaded',
        'parsing',
        'parsed',
        'chunking',
        'chunked',
        'embedding',
        'indexed',
      ])
      expect(steps.find((step) => step.current)?.key).toBe('parsing')
      expect(steps.find((step) => step.current)?.state).toBe('current')
    })

    it('已入库时全部段落都已走过', () => {
      const steps = progressSteps('indexed')

      // 末段是"当前所在的那一段",更早的段落都已走过
      expect(steps.every((step) => step.state === 'done' || step.state === 'current')).toBe(true)
      expect(steps.find((step) => step.current)?.key).toBe('indexed')
      expect(steps.slice(0, -1).every((step) => step.state === 'done')).toBe(true)
    })

    it('失败状态落在失败段,不伪造后续进度', () => {
      const steps = progressSteps('failed')

      expect(steps.find((step) => step.current)?.key).toBe('failed')
      expect(steps.find((step) => step.current)?.state).toBe('failed')
    })

    it('退位与删除是终态,不展开状态机段落', () => {
      expect(progressSteps('superseded')).toEqual([])
      expect(progressSteps('deleted')).toEqual([])
    })

    it('已切片状态给出可重试向量的落点', () => {
      const steps = progressSteps('chunked')

      expect(steps.find((step) => step.current)?.key).toBe('chunked')
      // 已切片之后就是向量化,不是终点
      expect(steps.map((step) => step.key)).toContain('embedding')
    })
  })

  describe('状态标签', () => {
    it('每个状态都有规范术语标签', () => {
      expect(statusLabel('uploaded')).toBe('已登记')
      expect(statusLabel('parsing')).toBe('解析中')
      expect(statusLabel('parsed')).toBe('已解析')
      expect(statusLabel('chunking')).toBe('切片中')
      expect(statusLabel('chunked')).toBe('已切片')
      expect(statusLabel('embedding')).toBe('向量化中')
      expect(statusLabel('indexed')).toBe('已入库')
      expect(statusLabel('failed')).toBe('失败')
      expect(statusLabel('superseded')).toBe('已退位')
      expect(statusLabel('deleted')).toBe('已删除')
    })

    it('标签按状态给出正确的呈现类型', () => {
      expect(statusTagType('indexed')).toBe('success')
      expect(statusTagType('failed')).toBe('danger')
      expect(statusTagType('superseded')).toBe('info')
      expect(statusTagType('deleted')).toBe('info')
      expect(statusTagType('chunked')).toBe('warning')
      expect(statusTagType('uploaded')).toBe('warning')
    })
  })

  describe('时间线', () => {
    const doc = {
      id: 'doc-1',
      file_name: '员工手册.md',
      file_type: 'md' as const,
      size_bytes: 128,
      tenant: 'default',
      owner_id: 'usr_owner',
      department: null,
      doc_type: null,
      version: 'v1',
      status: 'failed' as const,
      error_message: '解析失败:编码不支持',
      is_latest: true,
      page_count: null,
      element_count: null,
      chunk_count: null,
      created_at: '2026-10-31T08:00:00Z',
      parsed_at: null,
      chunked_at: null,
      indexed_at: null,
      failed_at: '2026-10-31T08:05:00Z',
      superseded_at: null,
      deleted_at: null,
    }

    it('时间线只列出真的发生过的时刻', () => {
      const timeline = buildTimeline(doc)

      expect(timeline.map((entry) => entry.key)).toEqual(['created', 'failed'])
    })

    it('时间线按发生顺序排列', () => {
      const timeline = buildTimeline({
        ...doc,
        status: 'indexed',
        parsed_at: '2026-10-31T08:01:00Z',
        chunked_at: '2026-10-31T08:03:00Z',
        indexed_at: '2026-10-31T08:04:00Z',
        failed_at: null,
      })

      expect(timeline.map((entry) => entry.key)).toEqual([
        'created',
        'parsed',
        'chunked',
        'indexed',
      ])
    })

    it('时间线条目带可读时刻', () => {
      const timeline = buildTimeline(doc)

      expect(timeline[0].label).toBe('创建')
      expect(timeline[0].at).toBe('2026-10-31T08:00:00Z')
      expect(timeline[1].label).toBe('失败')
    })

    it('退位与删除也在时间线上', () => {
      const timeline = buildTimeline({
        ...doc,
        status: 'deleted',
        failed_at: null,
        deleted_at: '2026-10-31T09:00:00Z',
      })

      expect(timeline.map((entry) => entry.key)).toEqual(['created', 'deleted'])
    })
  })

  describe('解析元素分组', () => {
    it('按元素类型折叠分组,保留原有顺序', () => {
      const groups = groupElementsByType([
        { element_type: 'title', content: '第一章', order_index: 0, page_number: null, heading_level: 1, line: null },
        { element_type: 'paragraph', content: '正文', order_index: 1, page_number: 1, heading_level: null, line: 3 },
        { element_type: 'title', content: '第二章', order_index: 2, page_number: null, heading_level: 1, line: null },
      ])

      expect(groups.map((group) => group.type)).toEqual(['title', 'paragraph'])
      expect(groups[0].elements.map((element) => element.content)).toEqual(['第一章', '第二章'])
      expect(groups[1].elements).toHaveLength(1)
    })

    it('没有元素时分组为空', () => {
      expect(groupElementsByType([])).toEqual([])
    })
  })

  describe('进度段的形状', () => {
    it('每段带 key、文案与状态', () => {
      const steps: ProgressStep[] = progressSteps('uploaded')

      // 当前所在的那一段就是 uploaded 自己
      expect(steps[0]).toMatchObject({ key: 'uploaded', label: '已登记', state: 'current' })
      expect(steps[1].state).toBe('pending')
    })
  })
})
