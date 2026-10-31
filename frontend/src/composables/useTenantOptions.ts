/**
 * 租户选项的数据源:全局租户选择器的选项**只从服务端取**。
 *
 * 为什么不在前端另存一份:后端文档清单里的租户维度才是"真实存在的租户";
 * 前端若自己攒一份,就会出现"前端所知的租户"与"后端所知的租户"两套真相
 * (ADR-0015)。
 *
 * 放在**应用外壳**这一层(而不是某个页面):租户选择器在每个页面都在,选项
 * 也应当在每个页面都在 —— 挂在某个页面上会让"没进过那个页面"的其他页面
 * 看不到租户选项。
 *
 * 取数不受当前租户筛选影响:若用筛选后的清单派生,选中某个租户后其它租户
 * 就会从下拉里消失,用户再也切不回去。
 */
import { useQuery } from '@tanstack/vue-query'
import { watch } from 'vue'

import { listDocuments } from '@/api'
import { useTenantStore } from '@/stores/tenant'

/** 取租户选项的清单条数:够放下常见规模的租户集合。 */
const TENANT_SOURCE_LIMIT = 200

export function useTenantOptions(): void {
  const tenantStore = useTenantStore()

  const sourceQuery = useQuery({
    queryKey: ['documents', 'tenant-source'],
    queryFn: () => listDocuments({ limit: TENANT_SOURCE_LIMIT }),
  })

  // 服务端返回的真实租户维度并入选项集
  watch(
    () => sourceQuery.data.value?.items,
    (items) => {
      if (items) {
        tenantStore.mergeOptions([...new Set(items.map((item) => item.tenant))])
      }
    },
    { immediate: true },
  )
}
