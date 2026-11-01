/**
 * 知识库文档页的纯逻辑:扩展名校验、入库进度、状态标签、时间线与解析元素分组。
 *
 * 单独成模块是为了让这些规则**可测且唯一**:界面文案(状态标签、进度段文案)
 * 散落在组件里就容易漂移;收在一处,组件只管渲染。
 *
 * 术语遵循 `GLOSSARY.md`:知识库文档 / 入库进度 / 切片 / 解析元素;
 * 不使用 `_Avoid_` 清单里的同义词(文件 / 片段 / 上传进度)。
 */
import type { DocumentStatus, DocumentView, ElementView } from '@/types/documents'

/**
 * 支持的扩展名:txt / md / html / docx / pdf,`markdown` 与 `htm` 是别名。
 *
 * 与后端 `DocumentFormat.from_filename` 同口径(后端把 `.markdown` 归为 md、
 * `.htm` 归为 html);前端在**选择文件时**就校验,不让用户白等一次上传失败。
 */
export const SUPPORTED_EXTENSIONS: readonly string[] = [
  'txt',
  'md',
  'markdown',
  'html',
  'htm',
  'docx',
  'pdf',
]

/** 取文件名的小写扩展名;无扩展名返回空串。 */
export function extensionOf(fileName: string): string {
  const base = fileName.split(/[\\/]/).pop() ?? ''
  const dot = base.lastIndexOf('.')
  if (dot < 0 || dot === base.length - 1) {
    return ''
  }
  return base.slice(dot + 1).toLowerCase()
}

/** 扩展名是否受支持(大小写不敏感)。 */
export function isSupportedExtension(fileName: string): boolean {
  return SUPPORTED_EXTENSIONS.includes(extensionOf(fileName))
}

/** 状态标签的呈现类型(对应 Element Plus 的 tag type)。 */
export type StatusTagType = 'success' | 'warning' | 'info' | 'danger'

/** 状态 → 规范术语标签。 */
const STATUS_LABELS: Record<DocumentStatus, string> = {
  uploaded: '已登记',
  parsing: '解析中',
  parsed: '已解析',
  chunking: '切片中',
  chunked: '已切片',
  embedding: '向量化中',
  indexed: '已入库',
  failed: '失败',
  superseded: '已退位',
  deleted: '已删除',
}

/** 状态 → 界面标签文案。 */
export function statusLabel(status: DocumentStatus): string {
  return STATUS_LABELS[status] ?? status
}

/** 状态 → 标签呈现类型。 */
export function statusTagType(status: DocumentStatus): StatusTagType {
  if (status === 'indexed') {
    return 'success'
  }
  if (status === 'failed') {
    return 'danger'
  }
  if (status === 'superseded' || status === 'deleted') {
    return 'info'
  }
  return 'warning'
}

/**
 * 入库进度的状态机主链。
 *
 * **不画百分比进度条**:状态机有分支(失败)与重试点(重试向量化),编造
 * 百分比是撒谎。界面按"当前所在的那一段"呈现。
 */
const PROGRESS_CHAIN: DocumentStatus[] = [
  'uploaded',
  'parsing',
  'parsed',
  'chunking',
  'chunked',
  'embedding',
  'indexed',
]

/** 进度段的呈现状态。 */
export type ProgressState = 'done' | 'current' | 'pending' | 'failed'

/** 入库进度的一段。 */
export interface ProgressStep {
  /** 状态机的状态键。 */
  key: DocumentStatus
  /** 界面文案。 */
  label: string
  /** 该段的呈现状态。 */
  state: ProgressState
  /** 该段是否就是当前所在的那一段。 */
  current: boolean
}

/**
 * 按状态机位置展开入库进度。
 *
 * `superseded` / `deleted` 是生命周期终态,不在主链上 —— 展开不出有意义的
 * 进度段,返回空数组让界面改述"已退位 / 已删除"。
 */
export function progressSteps(status: DocumentStatus): ProgressStep[] {
  if (status === 'superseded' || status === 'deleted') {
    return []
  }
  if (status === 'failed') {
    return [
      { key: 'failed', label: STATUS_LABELS.failed, state: 'failed', current: true },
    ]
  }
  const currentIndex = PROGRESS_CHAIN.indexOf(status)
  return PROGRESS_CHAIN.map((key, index) => {
    const isCurrent = index === currentIndex
    const state: ProgressState = isCurrent ? 'current' : index < currentIndex ? 'done' : 'pending'
    return { key, label: STATUS_LABELS[key], state, current: isCurrent }
  })
}

/** 时间线上一个节点的键。 */
export type TimelineKey =
  | 'created'
  | 'parsed'
  | 'chunked'
  | 'indexed'
  | 'failed'
  | 'superseded'
  | 'deleted'

/** 时间线上的一个节点。 */
export interface TimelineEntry {
  /** 节点的键。 */
  key: TimelineKey
  /** 界面文案。 */
  label: string
  /** 发生时刻(ISO 字符串);节点存在即有时刻。 */
  at: string
}

/**
 * 由档案构造生命周期时间线。
 *
 * **只列真的发生过的时刻**:档案里的时间字段为 `null` 即"这一步没走到",
 * 不给它编一个时刻。时间线按发生顺序排列。
 */
export function buildTimeline(document: DocumentView): TimelineEntry[] {
  const entries: Array<{ key: TimelineKey; label: string; at: string | null }> = [
    { key: 'created', label: '创建', at: document.created_at },
    { key: 'parsed', label: '解析', at: document.parsed_at },
    { key: 'chunked', label: '切片', at: document.chunked_at },
    { key: 'indexed', label: '索引', at: document.indexed_at },
    { key: 'failed', label: '失败', at: document.failed_at },
    { key: 'superseded', label: '退位', at: document.superseded_at },
    { key: 'deleted', label: '删除', at: document.deleted_at },
  ]
  return entries
    .filter((entry): entry is { key: TimelineKey; label: string; at: string } => entry.at !== null)
    .map((entry) => ({ key: entry.key, label: entry.label, at: entry.at }))
}

/** 解析元素按类型折叠分组后的一组。 */
export interface ElementGroup {
  /** 元素类型(契约值:title / paragraph / table / list / code)。 */
  type: ElementView['element_type']
  /** 该组的元素。 */
  elements: ElementView[]
}

/** 解析元素类型的界面文案。 */
const ELEMENT_LABELS: Record<ElementView['element_type'], string> = {
  title: '标题',
  paragraph: '段落',
  table: '表格',
  list: '列表',
  code: '代码',
}

/** 解析元素类型的界面文案。 */
export function elementTypeLabel(type: ElementView['element_type']): string {
  return ELEMENT_LABELS[type] ?? type
}

/**
 * 解析元素按类型折叠分组。
 *
 * 分组顺序 = 类型首次出现的顺序(不是按类型字典序):解析产物的先后顺序
 * 是有信息的,把它抹平会让人对不上文档结构。
 */
export function groupElementsByType(elements: ElementView[]): ElementGroup[] {
  const groups: ElementGroup[] = []
  const byType = new Map<ElementView['element_type'], ElementGroup>()
  for (const element of elements) {
    const existing = byType.get(element.element_type)
    if (existing) {
      existing.elements.push(element)
      continue
    }
    const group: ElementGroup = { type: element.element_type, elements: [element] }
    byType.set(element.element_type, group)
    groups.push(group)
  }
  return groups
}
