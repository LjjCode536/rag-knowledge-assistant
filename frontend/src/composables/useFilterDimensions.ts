/**
 * 检索过滤维度的表单状态:部门 / 文档类型 / 版本 / 文档名。
 *
 * **为什么要这层**:这四个字段总是**一起**被填写、一起被拼成 `filters`
 * 提交(检索页与问答页都是同一套)。散成四个 `ref('')` 会让每个用到它们的
 * 页面都重复一遍"取值 → 判空 → 拼对象"的形状,且容易漏掉某一维。
 *
 * **租户不在这里**:租户来自登录身份,后端据 token 收窄;界面不再提供
 * 租户筛选,也不允许把租户塞进单次检索参数(ADR-0020)。
 */
import { computed, reactive } from 'vue'

import type { ChunkFilter } from '@/types/qa'

/** 过滤维度的表单状态。 */
export interface FilterDimensions {
  /** 部门。 */
  department: string
  /** 文档类型。 */
  docType: string
  /** 版本。 */
  version: string
  /** 文档 id。 */
  documentId: string
}

/**
 * 过滤维度的表单状态与提交口径。
 *
 * 这些维度只是客户端收窄条件;授权范围由服务端按主体构造,越界返回 403。
 */
export function useFilterDimensions() {
  const dimensions = reactive<FilterDimensions>({
    department: '',
    docType: '',
    version: '',
    documentId: '',
  })

  /**
   * 提交用的过滤对象。
   *
   * **空值不参与**:空串按"未提供"处理(与后端路由层的空值归一一致)——
   * 清空的输入框不该变成"匹配空字符串"的约束。
   */
  const filters = computed<ChunkFilter>(() => ({
    ...(dimensions.department ? { department: dimensions.department } : {}),
    ...(dimensions.docType ? { doc_type: dimensions.docType } : {}),
    ...(dimensions.version ? { version: dimensions.version } : {}),
    ...(dimensions.documentId ? { document_id: dimensions.documentId } : {}),
  }))

  /** 是否加了过滤维度(全空则不加)。 */
  const hasDimensions = computed<boolean>(
    () =>
      Boolean(dimensions.department) ||
      Boolean(dimensions.docType) ||
      Boolean(dimensions.version) ||
      Boolean(dimensions.documentId),
  )

  return { dimensions, filters, hasDimensions }
}
