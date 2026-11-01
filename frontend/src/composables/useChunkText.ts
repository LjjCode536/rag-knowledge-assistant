/**
 * 引用切片原文的取数:让用户能亲眼核实"答案不是编的"。
 *
 * **契约背景**:`POST /api/v1/chat` 的 `Citation` 只有定位信息(文档 / 标题 /
 * 页码 / 分数),**不含切片正文** —— 回答正文由生成器基于切片内容写成,后端
 * 不把切片原文再回传一遍。要看到原文,只能按 `document_id` 取该文档的
 * 全部切片,再按 `chunk_id` 定位到那一条。
 *
 * **取数策略**:展开某条引用时,才按该引用的 `document_id` 拉一次切片清单
 * 并缓存(同一文档的多次展开只发一次请求)。切片原文是知识库的正文内容,
 * 体积可控 —— 这里刻意不走"按 chunk_id 单查"的窄端点,因为后端没有那个端点。
 *
 * 术语遵循 `GLOSSARY.md`:看到的是**切片原文**(不是"片段""出处")。
 */
import { useQuery } from '@tanstack/vue-query'
import { computed, type ComputedRef, type MaybeRefOrGetter, toValue } from 'vue'

import { listChunks } from '@/api'

/** 单条引用的切片原文取数结果。 */
export interface ChunkTextResult {
  /** 切片原文;尚未取到或取不到时为 null。 */
  text: ComputedRef<string | null>
  /** 是否正在取数。 */
  isLoading: ComputedRef<boolean>
  /** 取数是否失败(文档已删除 / 网络问题)。 */
  isError: ComputedRef<boolean>
}

/**
 * 取一条引用的切片原文。
 *
 * **只能在 setup 顶层调用**(它内部用了 `useQuery`);引用条数会变时,调用
 * 方的循环必须稳定 —— 实践上由**父组件**统一取数、再把结果传下来,
 * 不在 `v-for` 里直接调本函数。
 */
export function useChunkText(
  documentId: MaybeRefOrGetter<string | undefined>,
  chunkId: MaybeRefOrGetter<string | undefined>,
): ChunkTextResult {
  const chunksQuery = useQuery({
    queryKey: computed(() => ['chunks', toValue(documentId)] as const),
    queryFn: () => listChunks(toValue(documentId)!),
    enabled: computed(() => Boolean(toValue(documentId))),
    // 切片原文入库后基本不变:短时缓存,避免反复回看同一文档时反复发请求。
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

  /** 原文是否已加载完毕(用于区分"还没取到"与"确实没有正文")。 */
  const isLoading = computed<boolean>(
    () => chunksQuery.isPending.value && Boolean(toValue(documentId)),
  )

  /** 取数失败(文档已删除 / 网络问题)。 */
  const isError = computed<boolean>(() => chunksQuery.isError.value)

  return { text, isLoading, isError }
}
