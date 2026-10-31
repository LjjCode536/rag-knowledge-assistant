<script setup lang="ts">
/**
 * 错误详情区:展示错误码、消息、详情与**可复制的 trace_id**。
 *
 * 呼应后端"trace_id 贯穿响应头、响应体与全部日志"的可观测性契约:
 * 维护者拿 trace_id 就能精确复现当次请求。
 */
import { computed, ref } from 'vue'

import { isApiError } from '@/api'

const props = defineProps<{
  /** 错误对象:后端错误码 / 消息 / 详情 / trace_id。 */
  error: unknown
}>()

const copied = ref(false)

const apiError = computed(() => (isApiError(props.error) ? props.error : null))

/** 错误码:类型化错误取 `code`,其它异常取构造名。 */
const code = computed<string>(() => apiError.value?.code ?? 'UNKNOWN_ERROR')

/** 错误消息。 */
const message = computed<string>(() => {
  if (apiError.value) {
    return apiError.value.message
  }
  return props.error instanceof Error ? props.error.message : String(props.error)
})

/** trace_id:可复制。 */
const traceId = computed<string>(() => apiError.value?.traceId ?? '')

/** 错误详情:对象则序列化展示。 */
const details = computed<string>(() => {
  const value = apiError.value?.details
  if (!value) {
    return ''
  }
  return JSON.stringify(value, null, 2)
})

async function copyTraceId(): Promise<void> {
  if (!traceId.value) {
    return
  }
  try {
    await navigator.clipboard.writeText(traceId.value)
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    // 剪贴板不可用(非安全上下文 / 权限拒绝):用户仍可手动选中复制
    copied.value = false
  }
}
</script>

<template>
  <el-alert
    type="error"
    :closable="false"
    show-icon
  >
    <template #title>
      <span data-testid="error-code">{{ code }}</span>
      <span style="margin-left: 8px">{{ message }}</span>
    </template>
    <div
      v-if="traceId"
      style="margin-top: 6px"
    >
      <span style="font-size: 12px; color: var(--el-text-color-secondary)">trace_id:</span>
      <span
        class="trace-id"
        data-testid="error-trace-id"
      >{{ traceId }}</span>
      <el-button
        size="small"
        text
        type="primary"
        style="margin-left: 8px"
        @click="copyTraceId"
      >
        {{ copied ? '已复制' : '复制' }}
      </el-button>
    </div>
    <pre
      v-if="details"
      class="trace-id"
      data-testid="error-details"
      style="margin: 6px 0 0; white-space: pre-wrap"
    >{{ details }}</pre>
  </el-alert>
</template>
