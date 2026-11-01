/**
 * 引用的可核实信息:文档名 + 切片原文 —— 让用户能亲眼核实"答案不是编的"。
 *
 * **契约背景**:`POST /api/v1/chat` 的 `Citation` 只有定位信息(文档 / 标题 /
 * 页码 / 分数),**不含切片正文** —— 回答正文由生成器基于切片内容写成,后端
 * 不把切片原文再回传一遍。要看到原文,只能按 `document_id` 取该文档的
 * 全部切片,再按 `chunk_id` 定位到那一条。
 *
 * **文档名也可能是缺的**:回看历史提问时,引用来自查询日志的命中,而那份
 * 契约不带文档名。此时按 `document_id` 查文档档案补上(后端有该端点,
 * 不必改后端)。
 *
 * **取数策略**:展开某条引用时,才按该引用的 `document_id` 取切片清单与
 * 文档档案,并缓存(同一文档的多次展开只发一次请求)。切片原文是知识库的
 * 正文内容,体积可控 —— 这里刻意不走"按 chunk_id 单查"的窄端点,因为后端
 * 没有那个端点。
 *
 * 术语遵循 `GLOSSARY.md`:看到的是**切片原文**(不是"片段""出处")。
 */
import { useQuery } from '@tanstack/vue-query'
import { computed, type ComputedRef, type MaybeRefOrGetter, toValue } from 'vue'

import { getDocument, listChunks } from '@/api'

/** 一条引用的可核实信息。 */
export interface CitationDetail {
  /** 切片原文;尚未取到或取不到时为 null。 */
  text: ComputedRef<string | null>
  /** 文档名;引用自带时直接用,否则按文档档案补,补不到时为 null。 */
  fileName: ComputedRef<string | null>
  /** 是否正在取数。 */
  isLoading: ComputedRef<boolean>
  /** 取数是否失败(文档已删除 / 网络问题)。 */
  isError: ComputedRef<boolean>
}

/**
 * 取一条引用的可核实信息(文档名 + 切片原文)。
 *
 * **只能在 setup 顶层调用**(它内部用了 `useQuery`)。引用条数会变时,调用
 * 方的循环必须稳定 —— 所以**不要在 `v-for` 里直接调本函数**:那样调用
 * 次数会随数据变化,hook 的调用顺序就不稳定了。实践上由每条引用各自
 * 渲染成一个子组件(`CitationItem.vue`),每个组件实例内部调一次 ——
 * 组件实例的个数可以变,hook 顺序在单个实例内始终稳定。
 *
 * @param documentId 引用所属的知识库文档 id
 * @param chunkId 被引用的切片 id
 * @param knownFileName 引用里已有的文档名(有就不必再查)
 */
export function useCitationDetail(
  documentId: MaybeRefOrGetter<string | undefined>,
  chunkId: MaybeRefOrGetter<string | undefined>,
  knownFileName: MaybeRefOrGetter<string | null | undefined> = undefined,
): CitationDetail {
  /** 切片原文要展开才取(正文体积大,没展开不必取)。 */
  const chunksEnabled = computed<boolean>(() => Boolean(toValue(chunkId)))

  const chunksQuery = useQuery({
    queryKey: computed(() => ['chunks', toValue(documentId)] as const),
    queryFn: () => listChunks(toValue(documentId)!),
    enabled: chunksEnabled,
    // 切片原文入库后基本不变:短时缓存,避免反复回看同一文档时反复发请求。
    staleTime: 60_000,
  })

  /**
   * 文档档案只在"引用没带文档名"时才要查。
   *
   * **不受展开与否影响**:文档名是引用列表里的一栏,用户没展开切片原文
   * 时也该看到它 —— 否则列表里的"文件名"一栏在回看历史时是空的。
   */
  const needDocument = computed<boolean>(
    () => !toValue(knownFileName) && Boolean(toValue(documentId)),
  )

  const documentQuery = useQuery({
    queryKey: computed(() => ['document', toValue(documentId)] as const),
    queryFn: () => getDocument(toValue(documentId)!),
    enabled: needDocument,
    staleTime: 60_000,
  })

  /** 该引用的切片原文;取不到时为 null(界面据此提示"原文暂不可用")。 */
  const text = computed<string | null>(() => {
    const target = toValue(chunkId)
    if (!target) {
      return null
    }
    const chunks = chunksQuery.data.value?.chunks ?? []
    return chunks.find((chunk) => chunk.chunk_id === target)?.content ?? null
  })

  /** 文档名:引用自带就用自带的,否则用文档档案补的,都没有则 null。 */
  const fileName = computed<string | null>(() => {
    const known = toValue(knownFileName)
    if (known) {
      return known
    }
    return documentQuery.data.value?.file_name ?? null
  })

  /** 原文是否已加载完毕(用于区分"还没取到"与"确实没有正文")。 */
  const isLoading = computed<boolean>(
    () => chunksEnabled.value && chunksQuery.isPending.value,
  )

  /** 取数失败(文档已删除 / 网络问题)。 */
  const isError = computed<boolean>(() => chunksQuery.isError.value)

  return { text, fileName, isLoading, isError }
}
