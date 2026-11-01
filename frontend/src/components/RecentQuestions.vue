<script setup lang="ts">
/**
 * 最近提问侧栏:列出最近的提问,点开可回看当时的完整结果。
 *
 * **数据来源是查询日志**:后端每次问答都留一条可追溯记录(问题、答案、
 * 命中、拒答原因、延迟),这里取最近若干条,让用户能回看"自己问过什么、
 * 得到了什么"。
 *
 * **服务端分页的已知上限**:该端点只有 `limit` 没有 `offset`,故只取最近
 * 一屏,并在界面上如实标注(ADR-0015)。
 */
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'

import { listQueryLogs } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'

/** 侧栏取最近多少条:够回看,又不至于把侧栏撑爆。 */
const RECENT_LIMIT = 20

const props = defineProps<{
  /** 当前展示的问答标识(用于标出侧栏里的当前项)。 */
  currentQueryId?: string
}>()

const emit = defineEmits<{
  /** 点开一条最近提问:带上它的 `query_id`。 */
  open: [queryId: string]
}>()

const logsQuery = useQuery({
  queryKey: ['query-logs', 'recent'],
  queryFn: () => listQueryLogs(RECENT_LIMIT),
})

/** 最近提问列表(最近优先)。 */
const recentLogs = computed(() => logsQuery.data.value?.items ?? [])
</script>

<template>
  <aside class="recent-questions">
    <div class="recent-questions__header">
      <strong>最近提问</strong>
    </div>

    <ErrorDetails
      v-if="logsQuery.isError.value"
      :error="logsQuery.error.value"
    />

    <ul
      v-else
      class="recent-questions__list"
      data-testid="recent-questions"
    >
      <li
        v-for="log in recentLogs"
        :key="log.query_id"
      >
        <button
          type="button"
          class="recent-questions__item"
          :class="{ 'recent-questions__item--current': log.query_id === props.currentQueryId }"
          :data-testid="`recent-question-${log.query_id}`"
          @click="emit('open', log.query_id)"
        >
          <span class="recent-questions__question">{{ log.question }}</span>
          <span class="recent-questions__meta">
            <el-tag
              v-if="log.refused"
              type="warning"
              size="small"
            >
              拒答
            </el-tag>
            <span class="page-hint">{{ new Date(log.created_at).toLocaleString() }}</span>
          </span>
        </button>
      </li>
    </ul>

    <p
      v-if="recentLogs.length === 0 && !logsQuery.isPending.value"
      class="page-hint"
    >
      还没有提问记录。
    </p>
    <p
      class="page-hint"
      style="margin-top: 8px"
    >
      只取最近 {{ RECENT_LIMIT }} 条(后端该端点只有 limit、没有 offset);完整历史见查询日志页。
    </p>
  </aside>
</template>
