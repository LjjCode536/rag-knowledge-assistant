/**
 * 关键字输入的防抖:把"用户还在打字"与"去查一次"分开。
 *
 * 清单的关键字筛选会随输入框的值进查询键(Vue Query 见到新键就重新取数)。
 * 不防抖的话,打五个字就是五次请求 —— 后端不忙,前端也别替它忙。
 *
 * 返回**已防抖的值**:把它接进查询键与请求参数,请求就只在停顿后发一次。
 */
import { onScopeDispose, ref, watch, type Ref } from 'vue'

/** 停顿多久后发请求(毫秒)。 */
const DEFAULT_DELAY = 300

export function useDebounced<T>(source: Ref<T>, delay = DEFAULT_DELAY): Ref<T> {
  /** 已防抖的值:停顿 `delay` 毫秒后才跟上源值。 */
  const debounced = ref(source.value) as Ref<T>

  let timer: ReturnType<typeof setTimeout> | undefined

  watch(source, (value) => {
    if (timer !== undefined) {
      clearTimeout(timer)
    }
    timer = setTimeout(() => {
      debounced.value = value
    }, delay)
  })

  // 组件卸载时清掉未触发的定时器:避免卸载后仍改 ref
  onScopeDispose(() => {
    if (timer !== undefined) {
      clearTimeout(timer)
    }
  })

  return debounced
}
