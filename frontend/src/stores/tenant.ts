/**
 * 全局租户筛选(store):跨页面保持的客户端状态。
 *
 * 边界(ADR-0015):后端当前**没有用户、没有会话、没有鉴权**,任何调用方都能
 * 传任意 `tenant`。界面上的租户切换**不是**权限控制 —— 它只是一个筛选
 * 维度,前端如实呈现这一现状,不制造"已隔离"的错觉。
 *
 * 服务端状态(查询结果、统计、文档清单)归 TanStack Query;这里只放跨页面
 * 的客户端状态(当前租户筛选)。
 *
 * **租户集合只从服务端取**:选项来自后端文档清单返回的真实租户维度,不在
 * 前端另存一份 —— 否则"前端所知的租户"与"后端所知的租户"会变成两套
 * 真相。
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

export const useTenantStore = defineStore('tenant', () => {
  /** 当前选中的租户标识;空串表示"全部租户"(不加租户约束)。 */
  const current = ref<string>('')

  /** 已知租户选项:来自后端文档清单的真实租户维度。 */
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

  /**
   * 选中一个租户。
   *
   * 选中值若还不在选项集里(用户新建的、尚未有文档的租户),补进选项集 ——
   * 否则下拉里看不到当前选中项,界面与状态就不一致了。
   */
  function select(value: string): void {
    current.value = value
    if (value && !options.value.some((option) => option.value === value)) {
      options.value = [...options.value, { value, label: value }]
    }
  }

  /**
   * 用服务端返回的租户集合覆盖选项集。
   *
   * 只增不减:服务端返回的集合是"此刻真实存在的租户",但当前选中项可能是
   * 用户新建的、尚未有文档的租户 —— 直接替换会把选中项从下拉里弄丢。
   */
  function mergeOptions(values: string[]): void {
    const incoming = values.filter((value) => value.trim() !== '')
    const known = new Set(options.value.map((option) => option.value))
    for (const value of incoming) {
      if (!known.has(value)) {
        known.add(value)
        options.value = [...options.value, { value, label: value }]
      }
    }
  }

  return { current, options, filter, currentLabel, select, mergeOptions }
})
