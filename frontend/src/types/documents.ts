/**
 * 知识库文档相关的类型:契约以端 OpenAPI(`/openapi.json`)为准,手写声明。
 *
 * 术语遵循仓库根目录 `GLOSSARY.md`:知识库文档 / 切片 / 引用 / 查询日志 /
 * 拒答 / 入库进度;不使用 `_Avoid_` 清单里的同义词(文件 / 片段 / source /
 * 出处)。
 */

/** 知识库文档格式:决定解析器选择。 */
export type DocumentFormat = 'txt' | 'md' | 'html' | 'docx' | 'pdf'

/**
 * 知识库文档生命周期状态。
 *
 * 入库进度按 `uploaded → parsing → parsed → chunking → chunked → embedding
 * → indexed` 逐段推进,失败落在 `failed`;`superseded` 承接版本退位,
 * `deleted` 承接文档删除,两者均为终态。
 */
export type DocumentStatus =
  | 'uploaded'
  | 'parsing'
  | 'parsed'
  | 'chunking'
  | 'chunked'
  | 'embedding'
  | 'indexed'
  | 'failed'
  | 'superseded'
  | 'deleted'

/** 文档档案视图:状态机位置、业务元数据与失败原因。 */
export interface DocumentView {
  id: string
  file_name: string
  file_type: DocumentFormat
  size_bytes: number
  tenant: string
  /** 文档所有者主体 id;历史文档没有归属时为 `null`。 */
  owner_id: string | null
  department: string | null
  doc_type: string | null
  version: string
  status: DocumentStatus
  error_message: string | null
  is_latest: boolean
  page_count: number | null
  element_count: number | null
  chunk_count: number | null
  created_at: string
  parsed_at: string | null
  chunked_at: string | null
  indexed_at: string | null
  failed_at: string | null
  superseded_at: string | null
  deleted_at: string | null
}

/**
 * 解析元素类型:解析产物的结构化单元种类。
 *
 * 契约值取自后端 `ElementType` 枚举(title / paragraph / table / list / code)。
 */
export type ElementType = 'title' | 'paragraph' | 'table' | 'list' | 'code'

/** 解析元素视图:类型 + 内容 + 定位元数据。 */
export interface ElementView {
  element_type: ElementType
  content: string
  order_index: number
  page_number: number | null
  heading_level: number | null
  line: number | null
}

/** 解析产物视图:文档 id、页数与元素。 */
export interface ElementsView {
  document_id: string
  page_count: number | null
  elements: ElementView[]
}

/**
 * 知识库文档清单:符合条件的总数 + 当前页条数。
 *
 * `total` 是符合筛选条件的总数(不受分页影响),故可据此算总页数。
 */
export interface DocumentList {
  total: number
  limit: number
  offset: number
  items: DocumentView[]
}

/**
 * 文档授权主体:一名用户、一个部门或整个租户(ADR-0021)。
 *
 * 租户级授权就是「发布到租户」:它仍挂在某份逻辑文档上,不是全库放行。
 */
export type GrantSubjectType = 'user' | 'department' | 'tenant'

/** 一条文档授权:以逻辑文档(租户 + 文档名)为键,版本退位后继续有效。 */
export interface DocumentGrant {
  grant_id: string
  tenant: string
  file_name: string
  subject_type: GrantSubjectType
  /** 租户级授权没有具体主体 id;用户 / 部门授权必有。 */
  subject_id: string | null
  granted_by: string
  created_at: string
}

/** 文档授权清单。 */
export interface GrantList {
  document_id: string
  total: number
  items: DocumentGrant[]
}

/** 添加授权的请求体:租户级授权不需要 `subject_id`。 */
export interface GrantCreateRequest {
  subject_type: GrantSubjectType
  subject_id?: string | null
}

/** 文档清单的查询参数:与端点契约一一对应;租户由登录身份决定。 */
export interface DocumentListParams {
  /** 按状态精确匹配。 */
  status?: DocumentStatus
  /** 按文件名子串匹配(大小写不敏感)。 */
  keyword?: string
  /** 按部门精确匹配。 */
  department?: string
  /** 按文档类型精确匹配。 */
  doc_type?: string
  /** 含退位与已删除的旧版本(默认只列现行版本)。 */
  include_history?: boolean
  /** 每页条数。 */
  limit?: number
  /** 偏移量。 */
  offset?: number
}
