<script setup lang="ts">
/**
 * 反馈页:差评列表 + 差评候选(按问题聚合,可导 CSV)。
 *
 * **CSV 导出破坏响应信封**:该端点返回带 UTF-8 BOM 的裸 CSV,不走统一
 * 拆解逻辑(见 `api/logs.ts` 的 `exportNegativeCandidatesCsv`)。
 */
import { useMutation, useQuery } from '@tanstack/vue-query'
import { computed, ref } from 'vue'

import { exportNegativeCandidatesCsv, listFeedback, listNegativeCandidates } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
import type { Feedback, GoldenCaseCandidate } from '@/types/logs'

/** 该端点的服务端上限:最近 100 条。 */
const SERVER_LIMIT = 100

const feedbackQuery = useQuery({
  queryKey: ['feedback'],
  queryFn: () => listFeedback({ limit: SERVER_LIMIT }),
})

const candidatesQuery = useQuery({
  queryKey: ['feedback', 'negative-candidates'],
  queryFn: () => listNegativeCandidates(SERVER_LIMIT),
})

const feedbacks = computed(() => feedbackQuery.data.value?.items ?? [])
const candidates = computed(() => candidatesQuery.data.value?.items ?? [])

/** 表格行类型:模板插槽里不便写类型标注,在此声明一次。 */
type FeedbackRow = Feedback
type CandidateRow = GoldenCaseCandidate

/** 导出的 CSV 文本:供用户另存。 */
const exportedCsv = ref('')

const exportMutation = useMutation({
  mutationFn: () => exportNegativeCandidatesCsv(SERVER_LIMIT),
  onSuccess: (csv) => {
    exportedCsv.value = csv
  },
})

/** 触发浏览器下载。 */
function downloadCsv(): void {
  if (!exportedCsv.value) {
    return
  }
  // BOM 已由后端带上前缀,Blob 直接按 UTF-8 存即可,Excel 能直开中文
  const blob = new Blob([exportedCsv.value], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'negative_candidates.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div>
    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>回答反馈</strong>
      </template>

      <ErrorDetails
        v-if="feedbackQuery.isError.value"
        :error="feedbackQuery.error.value"
      />

      <el-table
        v-loading="feedbackQuery.isPending.value"
        :data="feedbacks"
        style="width: 100%"
      >
        <el-table-column
          label="判定"
          width="100"
        >
          <template #default="{ row }: { row: FeedbackRow }">
            <el-tag
              :type="row.verdict === 'useful' ? 'success' : 'warning'"
              size="small"
            >
              {{ row.verdict === 'useful' ? '有用' : '无用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="query_id"
          label="关联问答"
          min-width="180"
        />
        <el-table-column
          label="说明"
          min-width="200"
        >
          <template #default="{ row }: { row: FeedbackRow }">
            {{ row.comment ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="时间"
          width="180"
        >
          <template #default="{ row }: { row: FeedbackRow }">
            {{ new Date(row.created_at).toLocaleString() }}
          </template>
        </el-table-column>
      </el-table>
      <p
        class="page-hint"
        style="margin-top: 8px"
      >
        界面只取最近 {{ SERVER_LIMIT }} 条(后端该端点只有 limit、没有 offset);完整历史见后端 API。
      </p>
    </el-card>

    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
          <strong>黄金用例候选(按问题聚类)</strong>
          <el-button
            size="small"
            :loading="exportMutation.isPending.value"
            data-testid="feedback-export"
            @click="exportMutation.mutate()"
          >
            导出 CSV
          </el-button>
        </div>
      </template>

      <ErrorDetails
        v-if="candidatesQuery.isError.value"
        :error="candidatesQuery.error.value"
      />
      <ErrorDetails
        v-if="exportMutation.isError.value"
        :error="exportMutation.error.value"
      />

      <el-table
        v-loading="candidatesQuery.isPending.value"
        :data="candidates"
        style="width: 100%"
      >
        <el-table-column
          prop="question"
          label="问题"
          min-width="240"
        />
        <el-table-column
          prop="occurrences"
          label="次数"
          width="90"
        />
        <el-table-column
          prop="failure"
          label="失败归因"
          width="140"
        />
        <el-table-column
          label="最近一次"
          width="180"
        >
          <template #default="{ row }: { row: CandidateRow }">
            {{ new Date(row.last_seen).toLocaleString() }}
          </template>
        </el-table-column>
      </el-table>

      <el-button
        v-if="exportedCsv"
        type="primary"
        plain
        style="margin-top: 12px"
        @click="downloadCsv"
      >
        保存导出的 CSV
      </el-button>
    </el-card>
  </div>
</template>
