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

import { useCitationDetail } from '@/composables/useCitationDetail'
import type { Citation } from '@/types/qa'

const props = defineProps<{
  /** 该条引用的定位信息。 */
  citation: Citation
  /** 是否为当前被正文角标定位到的引用。 */
  highlighted?: boolean
}>()

/** 是否已展开切片原文。 */
const expanded = ref(false)

/** 文档 id:查文档档案补文档名时要用,不依赖是否展开。 */
const documentId = computed<string | undefined>(() => props.citation.document_id)
/** 切片 id:只有展开了才去取原文。 */
const chunkId = computed<string | undefined>(() =>
  expanded.value ? props.citation.chunk_id : undefined,
)

const { text, fileName, isLoading, isError } = useCitationDetail(
  documentId,
  chunkId,
  computed(() => props.citation.file_name),
)

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
        文档名:引用自带就用自带的;回看历史时引用来自查询日志(那份契约
        不带文档名),展开后按文档档案补上。补不到时说"文档名不可查",
        不说"未知文档" —— 那会把"查不到"说成"没有"。
      -->
      <strong v-if="citation.file_name || fileName">{{ citation.file_name ?? fileName }}</strong>
      <span
        v-else
        class="page-hint"
      >
        文档名不可查
      </span>
      <span v-if="citation.title"> · {{ citation.title }}</span>
      <!-- 页码用 `!= null` 判空:页码 0 是合法的第 0 页,`v-if` 会把它当假值丢掉 -->
      <span v-if="citation.page_number !== null && citation.page_number !== undefined">
        · 第 {{ citation.page_number }} 页
      </span>
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

<style scoped>
/** 引用条目:被正文角标定位到时高亮。 */
.citation-item {
  margin-bottom: 8px;
  padding: 4px 8px;
  border-radius: 4px;
  /* 定位滚动时给上方留出余量,别让条目贴到视口顶边 */
  scroll-margin-top: 12px;
}

.citation-item--highlighted {
  background-color: var(--el-color-warning-light-9);
  outline: 1px solid var(--el-color-warning-light-7);
}

/** 引用展开后的切片原文:保留原始换行,便于逐句核对。 */
.citation-text {
  margin-top: 6px;
}

.citation-text__body {
  margin: 0;
  padding: 8px 10px;
  border-left: 3px solid var(--el-color-primary-light-5);
  background-color: var(--el-fill-color-lighter);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
