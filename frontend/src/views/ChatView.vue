<script setup lang="ts">
/**
 * 问答页:提问 → 回答 → 引用 → 反馈。
 *
 * 已知契约陷阱(ADR-0015):
 * - **上传/问答响应是缓冲 JSON**(后端 `/chat` 无流式):第一版用"请求完成
 *   后打字机动画"的假流式,总时长与后端真实耗时相同,只改善观感;
 * - **拒答用中性/警告色提示而非红色错误**,并说明原因;**引用列表为空时
 *   不渲染空引用区**;
 * - **反馈状态回显**:打开一次问答时按 `query_id` 查既有反馈。
 */
import { useMutation, useQuery } from '@tanstack/vue-query'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { ask, listFeedback, submitFeedback } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
import { useTenantStore } from '@/stores/tenant'
import type { ChatAnswer } from '@/types/qa'

const tenantStore = useTenantStore()

/** 问题输入。 */
const question = ref('')
/** top_k:检索返回的切片数上限。 */
const topK = ref(5)
/** 当前回答。 */
const answer = ref<ChatAnswer | null>(null)
/** 假流式的已展示长度(打字机动画)。 */
const revealed = ref(0)

/** 假流式:按固定节奏逐步展示回答文本。 */
const TYPING_INTERVAL_MS = 16
let typingTimer: number | undefined

function startTyping(text: string): void {
  stopTyping()
  revealed.value = 0
  if (!text) {
    return
  }
  typingTimer = window.setInterval(() => {
    if (revealed.value >= text.length) {
      stopTyping()
      return
    }
    revealed.value += 1
  }, TYPING_INTERVAL_MS)
}

function stopTyping(): void {
  if (typingTimer !== undefined) {
    window.clearInterval(typingTimer)
    typingTimer = undefined
  }
}

const askMutation = useMutation({
  mutationFn: () =>
    ask(question.value.trim(), {
      top_k: topK.value,
      filters: tenantStore.filter ? { tenant: tenantStore.filter } : undefined,
    }),
  onSuccess: (data) => {
    answer.value = data
    // 拒答的文案短,不必动画;有据回答才走打字机
    startTyping(data.refused ? '' : data.answer)
  },
})

/** 展示中的回答正文(打字机动画的当前长度)。 */
const displayedAnswer = computed<string>(() => {
  if (!answer.value) {
    return ''
  }
  if (answer.value.refused) {
    return answer.value.answer
  }
  return answer.value.answer.slice(0, revealed.value)
})

/** 拒答原因的中文说明。 */
const refusalExplanation = computed<string>(() => {
  switch (answer.value?.refusal_reason) {
    case 'no_hits':
      return '未检索到相关资料'
    case 'low_score':
      return '检索到的资料相关度不足'
    case 'generator_refused':
      return '生成器依据现有资料无法作答'
    default:
      return '资料不足以支撑回答'
  }
})

/** 引用列表:为空时不渲染。 */
const citations = computed(() => answer.value?.citations ?? [])

/** 当前回答的反馈状态(打开一次问答时按 query_id 查既有反馈)。 */
const feedbackQuery = useQuery({
  queryKey: computed(() => ['feedback', answer.value?.query_id] as const),
  queryFn: () => listFeedback({ query_id: answer.value!.query_id }),
  enabled: computed(() => Boolean(answer.value?.query_id)),
})

/** 已提交的反馈判定(用于回显按钮选中态)。 */
const submittedVerdict = computed<string | null>(() => {
  const items = feedbackQuery.data.value?.items ?? []
  return items.length > 0 ? items[0].verdict : null
})

const feedbackMutation = useMutation({
  mutationFn: (verdict: 'useful' | 'useless') => {
    if (!answer.value) {
      throw new Error('没有可反馈的回答')
    }
    return submitFeedback(answer.value.query_id, verdict)
  },
  onSuccess: () => {
    void feedbackQuery.refetch()
  },
})

/** 反馈说明。 */
const feedbackComment = ref('')

function onSubmitFeedback(verdict: 'useful' | 'useless'): void {
  feedbackMutation.mutate(verdict, {
    onSuccess: () => {
      feedbackComment.value = ''
    },
  })
}

watch(question, () => {
  stopTyping()
})

onBeforeUnmount(stopTyping)
</script>

<template>
  <div>
    <el-card
      class="page-section"
      shadow="never"
    >
      <el-form
        :inline="false"
        @submit.prevent="askMutation.mutate()"
      >
        <el-form-item label="问题">
          <el-input
            v-model="question"
            type="textarea"
            :rows="2"
            placeholder="用自然语言提问,例如:入职多久可以申请年假?"
            data-testid="chat-question"
            @keyup.ctrl.enter="askMutation.mutate()"
          />
        </el-form-item>
        <el-form-item label="检索条数">
          <el-input-number
            v-model="topK"
            :min="1"
            :max="50"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="askMutation.isPending.value"
            data-testid="chat-submit"
            @click="askMutation.mutate()"
          >
            提问
          </el-button>
        </el-form-item>
      </el-form>
      <p class="page-hint">
        提问时带上租户筛选:当前租户
        <strong>{{ tenantStore.currentLabel }}</strong>。租户是筛选维度,不是权限控制。
      </p>
    </el-card>

    <ErrorDetails
      v-if="askMutation.isError.value"
      :error="askMutation.error.value"
    />

    <el-card
      v-if="answer"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
          <strong>回答</strong>
          <el-tag
            v-if="answer.cache_hit"
            type="info"
            size="small"
          >
            答档缓存命中
          </el-tag>
          <span class="page-hint">
            延迟 {{ answer.latency_ms }} ms · 生成器 {{ answer.generator }}
          </span>
        </div>
      </template>

      <!-- 拒答:中性/警告色提示,不渲染引用区 -->
      <el-alert
        v-if="answer.refused"
        type="warning"
        :closable="false"
        show-icon
        data-testid="chat-refusal"
      >
        <template #title>
          {{ answer.answer }}
        </template>
        <div style="margin-top: 4px; font-size: 13px">
          原因:{{ refusalExplanation }}
        </div>
      </el-alert>

      <!-- 有据回答 -->
      <template v-else>
        <div
          style="white-space: pre-wrap; line-height: 1.8"
          data-testid="chat-answer"
        >
          {{ displayedAnswer }}
        </div>

        <el-divider content-position="left">
          引用
        </el-divider>
        <ol
          v-if="citations.length > 0"
          data-testid="chat-citations"
        >
          <li
            v-for="citation in citations"
            :key="citation.chunk_id"
            style="margin-bottom: 8px"
          >
            <div>
              <strong>{{ citation.file_name ?? '未知文档' }}</strong>
              <span v-if="citation.title"> · {{ citation.title }}</span>
              <span v-if="citation.page_number"> · 第 {{ citation.page_number }} 页</span>
              <span class="page-hint"> · 分数 {{ citation.score }}</span>
            </div>
          </li>
        </ol>
      </template>

      <el-divider content-position="left">
        回答反馈
      </el-divider>
      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
        <el-button
          :type="submittedVerdict === 'useful' ? 'primary' : 'default'"
          data-testid="feedback-useful"
          @click="onSubmitFeedback('useful')"
        >
          有用
        </el-button>
        <el-button
          :type="submittedVerdict === 'useless' ? 'warning' : 'default'"
          data-testid="feedback-useless"
          @click="onSubmitFeedback('useless')"
        >
          无用
        </el-button>
        <el-input
          v-model="feedbackComment"
          size="small"
          placeholder="一句话说明(可选)"
          style="max-width: 320px"
        />
      </div>
      <p
        v-if="submittedVerdict"
        class="page-hint"
        data-testid="feedback-state"
      >
        已提交反馈:{{ submittedVerdict === 'useful' ? '有用' : '无用' }}
      </p>
    </el-card>
  </div>
</template>
