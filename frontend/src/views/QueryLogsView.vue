<script setup lang="ts">
/**
 * 查询日志页:问答记录列表,展开看改写后的问题、命中、Token 与延迟。
 *
 * **服务端分页的已知上限**:该端点只有 `limit` 没有 `offset`,界面取最近
 * 100 条做前端分页,并**明确标注该上限**;完整历史见后端 API(ADR-0015)。
 */
import { useQuery } from '@tanstack/vue-query'
import { computed, ref } from 'vue'

import { listQueryLogs } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
import type { QueryLog } from '@/types/logs'

/** 该端点的服务端上限:最近 100 条。 */
const SERVER_LIMIT = 100

const logsQuery = useQuery({
  queryKey: ['query-logs'],
  queryFn: () => listQueryLogs(SERVER_LIMIT),
})

/** 前端分页:对已取回的最近 100 条做切片。 */
const page = ref(1)
const pageSize = ref(20)

const allLogs = computed(() => logsQuery.data.value?.items ?? [])
const total = computed(() => logsQuery.data.value?.total ?? 0)

const pagedLogs = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return allLogs.value.slice(start, start + pageSize.value)
})

/** 是否触到了服务端上限(界面上明确标注)。 */
const atServerLimit = computed(() => total.value > allLogs.value.length)

/** 生成器种类 → 界面文案(未登记的种类原样呈现,不隐藏事实)。 */
function generatorLabel(kind: string): string {
  if (kind === 'llm') {
    return 'LLM'
  }
  if (kind === 'rule_based') {
    return '规则版基线'
  }
  return kind
}

/** 表格行类型:模板插槽里不便写类型标注,在此声明一次。 */
type LogRow = QueryLog
</script>

<template>
  <div>
    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>查询日志</strong>
      </template>

      <ErrorDetails
        v-if="logsQuery.isError.value"
        :error="logsQuery.error.value"
      />

      <el-table
        v-loading="logsQuery.isPending.value"
        :data="pagedLogs"
        style="width: 100%"
      >
        <el-table-column type="expand">
          <template #default="{ row }: { row: LogRow }">
            <el-descriptions
              :column="1"
              border
              size="small"
            >
              <el-descriptions-item label="改写后的问题">
                {{ row.rewritten_question ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="多查询扩展">
                {{ row.expanded_questions.length ? row.expanded_questions.join('; ') : '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="生效过滤">
                {{ Object.keys(row.filters).length ? JSON.stringify(row.filters) : '无' }}
              </el-descriptions-item>
              <el-descriptions-item label="命中">
                <div
                  v-for="hit in row.hits"
                  :key="hit.chunk_id"
                >
                  #{{ hit.rank }} 分数 {{ hit.score }}
                  {{ hit.used ? '(进入证据)' : '(未进入证据)' }}
                  {{ hit.title ?? '' }}
                  <span v-if="hit.page_number">第 {{ hit.page_number }} 页</span>
                </div>
              </el-descriptions-item>
              <el-descriptions-item label="Token 用量">
                prompt {{ row.token_usage.prompt_tokens }} / completion
                {{ row.token_usage.completion_tokens }}
              </el-descriptions-item>
              <el-descriptions-item label="trace_id">
                <span class="trace-id">{{ row.trace_id }}</span>
              </el-descriptions-item>
            </el-descriptions>
          </template>
        </el-table-column>
        <el-table-column
          label="提问"
          min-width="220"
        >
          <template #default="{ row }: { row: LogRow }">
            {{ row.question }}
          </template>
        </el-table-column>
        <el-table-column
          label="拒答"
          width="90"
        >
          <template #default="{ row }: { row: LogRow }">
            <el-tag
              v-if="row.refused"
              type="warning"
              size="small"
            >
              是
            </el-tag>
            <span v-else>否</span>
          </template>
        </el-table-column>
        <el-table-column
          label="命中数"
          width="90"
        >
          <template #default="{ row }: { row: LogRow }">
            <span data-testid="query-log-hit-count">{{ row.hits.length }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="生成器"
          width="120"
        >
          <template #default="{ row }: { row: LogRow }">
            <span data-testid="query-log-generator">{{ generatorLabel(row.generator) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="Token 用量"
          width="110"
        >
          <template #default="{ row }: { row: LogRow }">
            {{ row.token_usage.total_tokens }}
          </template>
        </el-table-column>
        <el-table-column
          label="延迟"
          width="100"
        >
          <template #default="{ row }: { row: LogRow }">
            {{ row.latency_ms }} ms
          </template>
        </el-table-column>
        <el-table-column
          label="时间"
          width="180"
        >
          <template #default="{ row }: { row: LogRow }">
            {{ new Date(row.created_at).toLocaleString() }}
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="allLogs.length"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        style="margin-top: 12px"
      />
      <p
        class="page-hint"
        style="margin-top: 8px"
      >
        界面只取最近 {{ SERVER_LIMIT }} 条做前端分页(后端该端点只有 limit、没有 offset);完整历史见后端 API。
        <span v-if="atServerLimit">当前共 {{ total }} 条,已超出界面可查看的上限。</span>
      </p>
    </el-card>
  </div>
</template>
