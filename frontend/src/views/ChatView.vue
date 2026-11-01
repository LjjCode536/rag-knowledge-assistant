<script setup lang="ts">
/**
 * 问答页:提问 → 回答 → 引用 → 反馈,左侧是最近提问。
 *
 * 已知契约陷阱(ADR-0015):
 * - **上传/问答响应是缓冲 JSON**(后端 `/chat` 无流式):第一版用"请求完成
 *   后打字机动画"的假流式,总时长与后端真实耗时相同,只改善观感;
 * - **拒答用中性/警告色提示而非红色错误**,并说明原因;**引用列表为空时
 *   不渲染空引用区**;
 * - **反馈状态回显**:打开一次问答时按 `query_id` 查既有反馈。
 *
 * **"本次提问"与"回看历史"共用一屏**:侧栏的最近提问点开后,按
 * `query_id` 取查询日志,归一成同样的回答视图渲染(见 `answerView.ts`)。
 */
import { useMutation, useQuery } from '@tanstack/vue-query'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { ask, getQueryLog, listFeedback, submitFeedback } from '@/api'
import CitationItem from '@/components/CitationItem.vue'
import ErrorDetails from '@/components/ErrorDetails.vue'
import RecentQuestions from '@/components/RecentQuestions.vue'
import { answerViewOfChat, answerViewOfQueryLog, type AnswerView } from '@/components/answerView'
import { splitAnswerSegments } from '@/components/answerSegments'
import { describeEffectiveFilters } from '@/components/filterDimensions'
import { useFilterDimensions } from '@/composables/useFilterDimensions'
import { useTenantStore } from '@/stores/tenant'

const tenantStore = useTenantStore()

/** 问题输入。 */
const question = ref('')
/** top_k:检索返回的切片数上限。 */
const topK = ref(5)
/** 过滤维度:与后端 `ChunkFilter` 同名(部门 / 文档类型 / 版本 / 文档 id)。 */
const { dimensions, filters } = useFilterDimensions(() =>
  tenantStore.filter ? { tenant: tenantStore.filter } : {},
)
/** 当前展示的回答(本次提问或回看历史,归一后的形状)。 */
const answer = ref<AnswerView | null>(null)
/** 假流式的已展示长度(打字机动画)。 */
const revealed = ref(0)

/**
 * 打字机的推进节奏:每隔多久显现一个字。
 *
 * **总时长与后端真实耗时一致**(ADR-0015 的约定):后端 `/chat` 是缓冲
 * JSON,没有真实流式;界面用"请求完成后逐字显现"改善观感,动画的总时长
 * 取后端回传的 `latency_ms` —— 用户在真实耗时里等到了答案,而不是
 * "秒回一句话",假流式不欺骗等待体感。
 *
 * 每步显现几个字按"已用时长 / 总时长"折算:这样无论正文多长,动画都恰好
 * 在 `latency_ms` 处收尾。
 */
const TYPING_INTERVAL_MS = 16
let typingTimer: number | undefined
/** 本次动画的开始时刻(用于算剩余时间)。 */
let typingStartedAt = 0
/** 本次动画的总时长(ms)。 */
let typingDurationMs = 0

/**
 * 开始打字机动画。
 *
 * @param text 回答正文
 * @param durationMs 动画总时长:后端回传的真实耗时
 */
function startTyping(text: string, durationMs: number): void {
  stopTyping()
  revealed.value = 0
  if (!text) {
    return
  }
  typingDurationMs = Math.max(durationMs, TYPING_INTERVAL_MS)
  typingStartedAt = Date.now()
  typingTimer = window.setInterval(() => {
    if (revealed.value >= text.length) {
      stopTyping()
      return
    }
    // 按"已用时长 / 总时长"折算此刻应显现到的字数:动画恰好在
    // `durationMs` 处收尾,与正文长短无关。
    const elapsed = Date.now() - typingStartedAt
    const progress = Math.min(elapsed / typingDurationMs, 1)
    const target = Math.min(Math.ceil(progress * text.length), text.length)
    if (target <= revealed.value) {
      // 还不到该显现下一个字的时刻,等下一拍
      if (progress >= 1) {
        revealed.value = text.length
        stopTyping()
      }
      return
    }
    revealed.value = target
    if (progress >= 1) {
      revealed.value = text.length
      stopTyping()
    }
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
      filters: filters.value,
    }),
  onSuccess: (data) => {
    answer.value = answerViewOfChat(data)
    // 拒答的文案短,不必动画;有据回答才走打字机
    // 动画总时长 = 后端回传的真实耗时(假流式的约定,ADR-0015)
    startTyping(data.refused ? '' : data.answer, data.latency_ms)
  },
})

/** 当前回看历史提问的 `query_id`;空串表示没在回看。 */
const historyQueryId = ref('')

/** 点开一条最近提问。 */
function onOpenRecent(queryId: string): void {
  historyQueryId.value = queryId
  // 切换回答时清掉上一次的高亮与动画状态
  highlightedCitation.value = null
  stopTyping()
  revealed.value = 0
}

/**
 * 回看一次历史提问:按 `query_id` 取查询日志,渲染成同样的回答视图。
 *
 * **不重跑提问**:回看的是"当时的结果",重新提问可能因知识库变化或答档
 * 缓存失效而得到不同答案 —— 那不是用户要看的东西。
 */
const historyQuery = useQuery({
  queryKey: computed(() => ['query-log', historyQueryId.value] as const),
  queryFn: () => getQueryLog(historyQueryId.value!),
  enabled: computed(() => Boolean(historyQueryId.value)),
})

watch(
  () => historyQuery.data.value,
  (log) => {
    if (log) {
      answer.value = answerViewOfQueryLog(log)
      // 回看历史不做打字机:答案当时就已经生成好了,再演一遍是假的等待
      revealed.value = log.answer.length
    }
  },
)

/**
 * 展示中的回答正文(打字机动画的当前长度)。
 *
 * **拒答不动画**:拒答文案很短,逐字显现反而拖沓;
 * **回看历史不动画**:答案当时就已生成,再演一遍是假的等待。
 */
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

/**
 * 本次回答**实际生效**的检索口径。
 *
 * 取**后端回传的值**(`ChatAnswer.top_k` / `ChatAnswer.filters`),不是界面
 * 发出请求时填的那份 —— 后端可能有默认值、归一化或口径修正,只有后端
 * 知道的才是实际生效的。界面展示它,用户才能理解"答案为何不同"。
 */
const effectiveFilters = computed<Record<string, string>>(
  () => answer.value?.filters ?? {},
)

/**
 * 生效口径的文案:过滤维度逐项列出,便于逐条核对。
 *
 * **用规范术语而不是后端字段名**:后端字段名是契约(如 `doc_type`),界面
 * 文案要跟 `GLOSSARY.md` 的规范术语走(部门 / 文档类型 / 版本 / 文档名 /
 * 租户)—— 用户读的是术语,不是字段名。
 */
const effectiveFilterText = computed<string>(() =>
  describeEffectiveFilters(effectiveFilters.value),
)

/** 本次回答实际给出的引用编号(角标只指向这些编号)。 */
const citationIndexes = computed<ReadonlySet<number>>(
  () => new Set(citations.value.map((citation) => citation.index)),
)

/** 展示中的回答正文拆成文字段与引用角标段。 */
const answerSegments = computed(() =>
  splitAnswerSegments(displayedAnswer.value, citationIndexes.value),
)

/** 当前高亮的引用编号:点击正文角标后定位到的引用条目。 */
const highlightedCitation = ref<number | null>(null)

/**
 * 点击正文里的编号角标:定位到下方对应的引用条目。
 *
 * **定位 = 滚动到该条目并高亮**:只改个颜色不够 —— 引用列表可能很长,
 * 目标条目在屏幕外,用户点了角标却看不到自己点的是哪一条。故这里真的把
 * 元素滚进视野,再高亮它。
 */
function onCitationBadgeClick(index: number): void {
  highlightedCitation.value = index
  const target = document.getElementById(`citation-${index}`)
  // `scrollIntoView` 在个别环境(jsdom)不存在 —— 定位不到也不该让点击崩掉
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
}

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

/** 已提交的反馈说明(打开一次问答时回显既有说明)。 */
const submittedComment = computed<string | null>(() => {
  const items = feedbackQuery.data.value?.items ?? []
  return items.length > 0 ? items[0].comment : null
})

/**
 * 反馈说明。
 *
 * **后端上限 200 字**(`FeedbackIn.comment` 的 `max_length`):界面用
 * `maxlength` 挡住超长输入,免得用户白填一轮却收到 422。
 */
const feedbackComment = ref('')
/** 反馈说明的字数上限:与后端 `FeedbackIn.comment` 的约束一致。 */
const FEEDBACK_COMMENT_MAX = 200

const feedbackMutation = useMutation({
  mutationFn: (verdict: 'useful' | 'useless') => {
    if (!answer.value) {
      throw new Error('没有可反馈的回答')
    }
    // 说明只随「无用」提交:规格里的一句话说明是给差评用的 ——「有用」
    // 没什么要解释的(若用户填了,提交「有用」时不带上它,免得把一段
    // 抱怨挂在"有用"上)。
    const comment = verdict === 'useless' ? feedbackComment.value.trim() : ''
    return submitFeedback(
      answer.value.query_id,
      verdict,
      comment === '' ? undefined : comment.slice(0, FEEDBACK_COMMENT_MAX),
    )
  },
  onSuccess: () => {
    void feedbackQuery.refetch()
  },
})

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

/**
 * 打开一次已有问答时,输入框预填既有的反馈说明。
 *
 * 反馈是**覆盖式**的(后端对同一问答只保留现行反馈):预填让用户看到"上次
 * 写的是什么",改写再提交即可覆盖;留空则提交为"无说明"。
 *
 * **只在切换问答时预填**:若跟着 `submittedComment` 一直同步,用户在输入
 * 过程中提交的反馈会回填把正在写的内容冲掉。
 *
 * **既有反馈是异步取回的**:问答响应落地时反馈还没到,故在反馈取回后再
 * 填一次。`lastPrefilledQueryId` 记住已填过哪一次问答,避免重复覆盖用户
 * 刚写的内容;**反馈尚未取回时不算已填**(否则后到的既有说明填不进去)。
 */
const lastPrefilledQueryId = ref<string | null>(null)

function prefillFeedbackComment(queryId: string, comment: string | null): void {
  if (lastPrefilledQueryId.value === queryId) {
    return
  }
  // 反馈还没取回(请求在途):等它到了再填 —— 否则后到的既有说明填不进去
  if (feedbackQuery.isPending.value) {
    return
  }
  lastPrefilledQueryId.value = queryId
  feedbackComment.value = comment ?? ''
}

watch(
  () => answer.value?.query_id,
  (queryId) => {
    if (queryId) {
      prefillFeedbackComment(queryId, submittedComment.value)
    }
  },
  { immediate: true },
)

watch(submittedComment, (comment) => {
  const queryId = answer.value?.query_id
  if (queryId) {
    prefillFeedbackComment(queryId, comment)
  }
})

onBeforeUnmount(stopTyping)
</script>

<template>
  <div class="chat-layout">
    <!-- 最近提问侧栏 -->
    <RecentQuestions
      :current-query-id="answer?.query_id"
      @open="onOpenRecent"
    />

    <div class="chat-layout__main">
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
          <el-form-item label="过滤维度">
            <div style="display: flex; gap: 8px; flex-wrap: wrap">
              <el-input
                v-model="dimensions.department"
                size="small"
                placeholder="部门"
                style="max-width: 130px"
                data-testid="chat-filter-department"
              />
              <el-input
                v-model="dimensions.docType"
                size="small"
                placeholder="文档类型"
                style="max-width: 130px"
                data-testid="chat-filter-doc-type"
              />
              <el-input
                v-model="dimensions.version"
                size="small"
                placeholder="版本"
                style="max-width: 110px"
                data-testid="chat-filter-version"
              />
              <el-input
                v-model="dimensions.documentId"
                size="small"
                placeholder="文档 id"
                style="max-width: 200px"
                data-testid="chat-filter-document-id"
              />
            </div>
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

        <!-- 本次实际生效的检索口径(后端回传,不是界面发出的那一份) -->
        <p
          class="page-hint"
          data-testid="chat-effective-params"
        >
          本次生效口径:检索条数 {{ answer.top_k }} · 过滤维度 {{ effectiveFilterText }}
        </p>

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
            <template
              v-for="(segment, position) in answerSegments"
              :key="position"
            >
              <span v-if="segment.kind === 'text'">{{ segment.text }}</span>
              <button
                v-else
                type="button"
                class="citation-badge"
                :aria-label="`查看引用 ${segment.index}`"
                :data-testid="`citation-badge-${segment.index}`"
                @click="onCitationBadgeClick(segment.index)"
              >
                {{ segment.index }}
              </button>
            </template>
          </div>

          <el-divider content-position="left">
            引用
          </el-divider>
          <ol
            v-if="citations.length > 0"
            data-testid="chat-citations"
          >
            <CitationItem
              v-for="citation in citations"
              :key="citation.chunk_id"
              :citation="citation"
              :highlighted="highlightedCitation === citation.index"
            />
          </ol>
        </template>

        <!-- 检索命中:回看历史时可见(本次提问的响应不带命中明细) -->
        <template v-if="answer.hits.length > 0">
          <el-divider content-position="left">
            检索命中
          </el-divider>
          <div
            class="page-hint"
            data-testid="chat-hits"
          >
            <div
              v-for="hit in answer.hits"
              :key="hit.chunk_id"
            >
              #{{ hit.rank }} 分数 {{ hit.score }} ·
              {{ hit.used ? '进入证据' : '未进入证据' }}
              <span v-if="hit.title"> · {{ hit.title }}</span>
              <span v-if="hit.page_number !== null && hit.page_number !== undefined">
                · 第 {{ hit.page_number }} 页
              </span>
            </div>
          </div>
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
            placeholder="「无用」可附一句话说明(可选,最多 200 字)"
            :maxlength="FEEDBACK_COMMENT_MAX"
            show-word-limit
            style="max-width: 320px"
            data-testid="feedback-comment"
          />
        </div>
        <p
          v-if="submittedVerdict"
          class="page-hint"
          data-testid="feedback-state"
        >
          已提交反馈:{{ submittedVerdict === 'useful' ? '有用' : '无用' }}
          <span v-if="submittedComment"> · 说明:{{ submittedComment }}</span>
        </p>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
/** 问答页布局:左侧最近提问,右侧提问与回答。 */
.chat-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.chat-layout__main {
  flex: 1 1 auto;
  min-width: 0;
}

/**
 * 回答正文里的引用角标:小号上标按钮,点击定位到下方对应的引用条目。
 *
 * 用 `<button>` 而非 `<span>`:角标是**可交互的**(点击要定位),键盘可达
 * 是它作为交互控件的基本要求。
 */
.citation-badge {
  display: inline-block;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  margin: 0 2px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  background-color: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 11px;
  line-height: 14px;
  text-align: center;
  vertical-align: super;
  cursor: pointer;
}

.citation-badge:hover,
.citation-badge:focus-visible {
  background-color: var(--el-color-primary-light-7);
}

/** 窄屏下侧栏落到主列上方。 */
@media (width <= 900px) {
  .chat-layout {
    flex-direction: column;
  }
}
</style>
