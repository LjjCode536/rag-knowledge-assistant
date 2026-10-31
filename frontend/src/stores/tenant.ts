/**
 * 全局租户筛选(store):跨页面保持的客户端状态。
 *
 * 边界(ADR-0015):后端当前**没有用户、没有会话、没有鉴权**,任何调用方都能
 * 传任意 `tenant`。界面上的租户切换**不是**权限控制 —— 它只是一个筛选
 * 维度,前端如实呈现这一现状,不制造"已隔离"的错觉。
 *
 * 服务端状态(查询结果、统计、文档清单)归 TanStack Query;这里只放跨页面
 * 的客户端状态(当前租户筛选)。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

/** 租户选项:标识 + 显示名(显示名缺省同标识)。 */
export interface TenantOption {
  /** 租户标识:进入请求参数的 `tenant`。 */
  value: string
  /** 显示名:界面文案,缺省同标识。 */
  label: string
}

/** 缺省租户:后端上传端点的缺省值。 */
export const DEFAULT_TENANT = 'default'

export const useTenantStore = defineStore('tenant', () => {
  /** 当前选中的租户标识;空串表示"全部租户"(不加租户约束)。 */
  const current = ref<string>('')

  /** 已知租户选项:可新建、可选择;数据源后端文档清单(见 issue #20 后续)。 */
  const options = ref<TenantOption[]>([])

  /** 当前生效的租户参数:空串 → `undefined`(不加约束)。 */
  const filter = computed<string | undefined>(() => (current.value ? current.value : undefined))

  /** 当前租户的显示名。 */
  const currentLabel = computed<string>(() => {
    if (!current.value) {
      return '全部租户'
    }
    return options.value.find((option) => option.value === current.value)?.label ?? current.value
  })

  /** 选中一个租户。 */
  function select(value: string): void {
    current.value = value
  }

  /**
   * 新建一个租户选项并选中它。
   *
   * 前端新建只影响**本地选项集**:后端没有"租户注册表"这个概念,租户是
   * 上传时自带的元数据。选项集在拿到真实文档清单后由服务端数据覆盖。
   */
  function create(label: string): void {
    const value = label.trim()
    if (!value) {
      return
    }
    if (!options.value.some((option) => option.value === value)) {
      options.value = [...options.value, { value, label: value }]
    }
    current.value = value
  }

  /** 用服务端返回的租户集合覆盖选项集(保留当前选中项)。 */
  function replaceOptions(values: string[]): void {
    options.value = [...new Set(values.filter((value) => value.trim() !== ''))].map((value) => ({
      value,
      label: value,
    }))
  }

  return { current, options, filter, currentLabel, select, create, replaceOptions }
})
