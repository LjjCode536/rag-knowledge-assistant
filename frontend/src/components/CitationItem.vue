<script setup lang="ts">
/**
 * 单条引用:编号 + 定位信息(文档名 / 标题 / 页码 / 分数)+ 可展开的切片原文。
 *
 * **为什么要切片原文**:引用只说"这条资料来自哪儿",用户要亲眼看到原文
 * 才能核实回答不是编的。`Citation` 契约不含正文(见 `api/` 层的说明),
 * 故展开时按 `document_id` 取该文档的切片清单并定位到 `chunk_id`。
 *
 * **独立组件而非列表项内联**:取数要用 `useQuery`(hook),而引用条数会随
 * 回答变化 —— 在 `v-for` 里直接调 hook 会因调用顺序不稳定而失效。
 * 拆成子组件后,每条引用各自持有自己的取数状态。
 *
 * 术语遵循 `GLOSSARY.md`:展示的是**切片原文**(不是"片段""出处")。
 */
import { computed, ref } from 'vue'

import { useChunkText } from '@/composables/useChunkText'
import type { Citation } from '@/types/qa'

const props = defineProps<{
  /** 该条引用的定位信息。 */
  citation: Citation
  /** 是否为当前被正文角标定位到的引用。 */
  highlighted?: boolean
}>()

/** 是否已展开切片原文。 */
const expanded = ref(false)

/** 展开时才取数:不展开就不发请求。 */
const documentId = computed<string | undefined>(() =>
  expanded.value ? props.citation.document_id : undefined,
)
const chunkId = computed<string | undefined>(() =>
  expanded.value ? props.citation.chunk_id : undefined,
)

const { text, isLoading, isError } = useChunkText(documentId, chunkId)

/** 展开 / 收起。 */
function toggle(): void {
  expanded.value = !expanded.value
}
</script>

<template>
  <li
    :id="`citation-${citation.index}`"
    class="citation-item"
    :class="{ 'citation-item--highlighted': highlighted }"
    :data-testid="`citation-${citation.index}`"
    :data-highlighted="highlighted ? 'true' : 'false'"
  >
    <div style="display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap">
      <!--
        文档名缺失时如实说"文档名不可查",不说"未知文档":
        回看历史时引用来自查询日志,那份契约不带文档名 —— 界面不假装
        知道,也不把"查不到"说成"没有"。
      -->
      <strong v-if="citation.file_name">{{ citation.file_name }}</strong>
      <span
        v-else
        class="page-hint"
      >
        文档名不可查
      </span>
      <span v-if="citation.title"> · {{ citation.title }}</span>
      <span v-if="citation.page_number"> · 第 {{ citation.page_number }} 页</span>
      <span class="page-hint"> · 分数 {{ citation.score }}</span>
      <el-button
        size="small"
        text
        type="primary"
        :data-testid="`citation-expand-${citation.index}`"
        @click="toggle"
      >
        {{ expanded ? '收起切片原文' : '查看切片原文' }}
      </el-button>
    </div>
    <div
      v-if="expanded"
      class="citation-text"
      :data-testid="`citation-text-${citation.index}`"
    >
      <p
        v-if="isLoading"
        class="page-hint"
      >
        正在取回切片原文…
      </p>
      <p
        v-else-if="isError"
        class="page-hint"
      >
        切片原文暂不可用(文档可能已被删除)。
      </p>
      <pre
        v-else-if="text"
        class="citation-text__body"
      >{{ text }}</pre>
      <p
        v-else
        class="page-hint"
      >
        切片原文暂不可用。
      </p>
    </div>
  </li>
</template>
