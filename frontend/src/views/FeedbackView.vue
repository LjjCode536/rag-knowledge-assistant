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

/** 前端分页:后端该端点只有 `limit` 没有 `offset`,取回最近 100 条后在此切片。 */
const feedbackPage = ref(1)
const feedbackPageSize = ref(20)
const pagedFeedbacks = computed(() => {
  const start = (feedbackPage.value - 1) * feedbackPageSize.value
  return feedbacks.value.slice(start, start + feedbackPageSize.value)
})

const candidatePage = ref(1)
const candidatePageSize = ref(20)
const pagedCandidates = computed(() => {
  const start = (candidatePage.value - 1) * candidatePageSize.value
  return candidates.value.slice(start, start + candidatePageSize.value)
})

/** 表格行类型:模板插槽里不便写类型标注,在此声明一次。 */
type FeedbackRow = Feedback
type CandidateRow = GoldenCaseCandidate

/** 拒答原因 → 界面文案(未登记的原因原样呈现,不隐藏事实)。 */
function refusalReasonLabel(reason: string | null): string {
  switch (reason) {
    case 'no_hits':
      return '未检索到资料'
    case 'low_score':
      return '资料相关度不足'
    case 'generator_refused':
      return '生成器无法作答'
    case null:
      return '—'
    default:
      return reason
  }
}

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
        :data="pagedFeedbacks"
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
      <el-pagination
        v-model:current-page="feedbackPage"
        v-model:page-size="feedbackPageSize"
        :total="feedbacks.length"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        style="margin-top: 12px"
        @size-change="feedbackPage = 1"
      />
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
        :data="pagedCandidates"
        style="width: 100%"
      >
        <el-table-column type="expand">
          <template #default="{ row }: { row: CandidateRow }">
            <el-descriptions
              :column="1"
              border
              size="small"
            >
              <el-descriptions-item label="评论">
                <div
                  v-for="(comment, position) in row.comments"
                  :key="position"
                  data-testid="candidate-comment"
                >
                  {{ comment }}
                </div>
                <span v-if="row.comments.length === 0">—</span>
              </el-descriptions-item>
              <el-descriptions-item label="来源上下文">
                <div
                  v-for="origin in row.origins"
                  :key="origin.query_id"
                  data-testid="candidate-origin"
                  style="margin-bottom: 8px"
                >
                  <div>
                    问答 {{ origin.query_id }} ·
                    {{ origin.refused ? '拒答' : '已作答' }}
                    <span v-if="origin.refused">
                      ({{ refusalReasonLabel(origin.refusal_reason) }})
                    </span>
                  </div>
                  <div>
                    命中文档:
                    {{ origin.top_documents.length ? origin.top_documents.join('、') : '无' }}
                  </div>
                  <div v-if="origin.comment">
                    评论:{{ origin.comment }}
                  </div>
                  <div class="page-hint">
                    {{ new Date(origin.created_at).toLocaleString() }}
                  </div>
                </div>
                <span v-if="row.origins.length === 0">—</span>
              </el-descriptions-item>
            </el-descriptions>
          </template>
        </el-table-column>
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
          label="评论"
          min-width="200"
        >
          <template #default="{ row }: { row: CandidateRow }">
            {{ row.comments.length ? row.comments.join(' | ') : '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="最近一次"
          width="180"
        >
          <template #default="{ row }: { row: CandidateRow }">
            {{ new Date(row.last_seen).toLocaleString() }}
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="candidatePage"
        v-model:page-size="candidatePageSize"
        :total="candidates.length"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        style="margin-top: 12px"
        @size-change="candidatePage = 1"
      />

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
