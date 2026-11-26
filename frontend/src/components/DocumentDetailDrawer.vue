<script setup lang="ts">
/**
 * 知识库文档详情抽屉:概览 / 入库进度 / 时间线 / 切片预览 / 解析元素 / 操作。
 *
 * **为什么独立成组件**:抽屉有自己的关切(轮询档案、看进度、失败恢复、
 * 重试向量化、复制文档标识),与清单(筛选、分页、删除)不相干。合在一起
 * 就是一个什么都要改的大文件。
 *
 * 契约陷阱(ADR-0015):
 * - **入库进度靠轮询**:上传返回的是任务句柄,进度经档案查询得知;
 * - **失败不靠 HTTP 码识别**:`status == "failed"` + `error_message` 在
 *   响应体里,界面必须检查文档状态;
 * - **不画百分比进度条**:状态机有分支与重试点,百分比是编造的。
 *
 * 术语遵循 `GLOSSARY.md`:知识库文档 / 入库进度 / 切片 / 解析元素。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'

import { getDocument, getDocumentElements, listChunks, retryEmbedding, uploadDocument } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
import {
  SUPPORTED_EXTENSIONS,
  buildTimeline,
  elementTypeLabel,
  extensionOf,
  groupElementsByType,
  isSupportedExtension,
  progressSteps,
  statusLabel,
  statusTagType,
} from '@/components/documentLogic'
import { useAuthStore } from '@/stores/auth'
import type { DocumentStatus } from '@/types/documents'

const props = defineProps<{
  /** 打开的文档 id;`null` 表示抽屉关闭。 */
  documentId: string | null
}>()

const emit = defineEmits<{
  /** 抽屉关闭。 */
  close: []
  /** 重新上传成功:新档案的 id(旧档案已退位)。 */
  reuploaded: [documentId: string]
}>()

const queryClient = useQueryClient()
const authStore = useAuthStore()

/**
 * 入库进度的终态:到了就不再推进,轮询没有意义。
 *
 * `failed` 也算终态 —— 失败要靠重新上传 / 重试向量化恢复,不是靠等。
 */
const TERMINAL_STATUSES: DocumentStatus[] = ['indexed', 'failed', 'superseded', 'deleted']

/**
 * 当前档案的状态。
 *
 * 用 `ref` 而不是从 `detailQuery.data` 派生:`refetchInterval` 与查询本身
 * 互相引用会绕成环(查询键变了才能取数,取数才知道状态)。这里让查询结果
 * 回写这个 ref,轮询间隔只看它。
 */
const detailStatus = ref<DocumentStatus | undefined>(undefined)

/** 轮询间隔:终态停轮询 —— 不再推进的文档没有轮询价值,白打后端。 */
const detailPollInterval = computed<number | false>(() => {
  if (!props.documentId) {
    return false
  }
  const status = detailStatus.value
  return status && TERMINAL_STATUSES.includes(status) ? false : 2000
})

/** 详情抽屉的数据:按 id 查询单份档案。 */
const detailQuery = useQuery({
  queryKey: computed(() => ['document', props.documentId] as const),
  queryFn: () => getDocument(props.documentId!),
  enabled: computed(() => Boolean(props.documentId)),
  // 抽屉开着时轮询,以便看到入库进度推进;终态停轮询。
  refetchInterval: detailPollInterval,
})

// 查询结果回写状态:供轮询间隔判断是否已到终态
watch(
  () => detailQuery.data.value?.status,
  (status) => {
    detailStatus.value = status
  },
  { immediate: true },
)

/** 切片预览:全量切片。 */
const chunksQuery = useQuery({
  queryKey: computed(() => ['document', props.documentId, 'chunks'] as const),
  queryFn: () => listChunks(props.documentId!),
  enabled: computed(() => Boolean(props.documentId)),
})

/** 解析元素:结构化解析产物。 */
const elementsQuery = useQuery({
  queryKey: computed(() => ['document', props.documentId, 'elements'] as const),
  queryFn: () => getDocumentElements(props.documentId!),
  enabled: computed(() => Boolean(props.documentId)),
})

/** 解析元素按类型折叠分组。 */
const elementGroups = computed(() => groupElementsByType(elementsQuery.data.value?.elements ?? []))

/** 当前档案的入库进度段。 */
const progress = computed(() =>
  detailQuery.data.value ? progressSteps(detailQuery.data.value.status) : [],
)

/** 当前档案的生命周期时间线。 */
const timeline = computed(() =>
  detailQuery.data.value ? buildTimeline(detailQuery.data.value) : [],
)

/**
 * 重新上传:后端定义的同标识重试语义(同租户 + 同文档名 → 旧版本退位)。
 *
 * **失败原因在后端,重传内容在用户手上**:后端没有"取回原始文档"的端点,
 * 所以重新上传必须由用户重新挑一次同一份知识库文档 —— 界面据此在失败
 * 区块里放一个文件选择入口,而不是假设浏览器还留着上次的文件。
 */
const reuploadFile = ref<File | null>(null)

/** 重新上传所选文件的扩展名是否受支持。 */
const reuploadExtensionError = computed<string>(() => {
  const file = reuploadFile.value
  if (!file || isSupportedExtension(file.name)) {
    return ''
  }
  return `不支持的知识库文档格式:.${extensionOf(file.name) || '(无扩展名)'}`
})

/**
 * 重新上传的元数据:租户由登录身份决定(不再随表单提交),部门只有管理员
 * 可显式指定;成员只在档案部门与自己的部门一致时透传,避免越界被拒。
 *
 * 后端的文档身份是 `(租户, 文件名)` —— 租户不再随表单提交,同标识完全由
 * 登录主体决定。
 */
const reuploadFields = computed(() => {
  const target = detailQuery.data.value
  const role = authStore.principal?.role
  const department =
    role === 'admin' || target?.department === authStore.principal?.department
      ? target?.department ?? undefined
      : undefined
  return {
    department,
    doc_type: target?.doc_type ?? undefined,
    version: target?.version ?? 'v1',
  }
})

const reuploadMutation = useMutation({
  mutationFn: (file: File) => uploadDocument(file, reuploadFields.value),
  onSuccess: (uploaded) => {
    reuploadFile.value = null
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
    void queryClient.invalidateQueries({ queryKey: ['document'] })
    // 旧档案已退位、新档案已登记:告诉父组件把抽屉指向新档案
    emit('reuploaded', uploaded.id)
  },
})

/** 重新上传:用户挑好同一份知识库文档后提交。 */
function onReupload(): void {
  const file = reuploadFile.value
  if (!file || reuploadExtensionError.value) {
    return
  }
  reuploadMutation.mutate(file)
}

/** 重新上传时挑文件:同样在**选中时**校验扩展名。 */
function onReuploadPicked(file: File | null): void {
  reuploadFile.value = file
}

/** 重试向量化:文档停在 `chunked` 时可单独重试,不必重传整份知识库文档。 */
const embedMutation = useMutation({
  mutationFn: (documentId: string) => retryEmbedding(documentId),
  onSuccess: () => {
    void queryClient.invalidateQueries({ queryKey: ['document'] })
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
  },
})

/** 字节数的可读呈现。 */
function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** 时刻的可读呈现。 */
function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

/** 文档标识是否已复制到剪贴板。 */
const idCopied = ref(false)

/** 复制文档标识(剪贴板不可用时用户仍可手动选中)。 */
async function copyDocumentId(id: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(id)
    idCopied.value = true
    window.setTimeout(() => {
      idCopied.value = false
    }, 1500)
  } catch {
    idCopied.value = false
  }
}
</script>

<template>
  <el-drawer
    :model-value="Boolean(documentId)"
    title="知识库文档详情"
    size="560px"
    @close="emit('close')"
  >
    <template v-if="detailQuery.data.value">
      <!-- 概览:格式 / 大小 / 部门 / 文档类型 / 版本 / 页数 / 元素数 / 切片数 -->
      <el-descriptions
        :column="1"
        border
        data-testid="documents-overview"
      >
        <el-descriptions-item label="文档名">
          {{ detailQuery.data.value.file_name }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag
            :type="statusTagType(detailQuery.data.value.status)"
            size="small"
          >
            {{ statusLabel(detailQuery.data.value.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="格式">
          {{ detailQuery.data.value.file_type }}
        </el-descriptions-item>
        <el-descriptions-item label="大小">
          {{ formatSize(detailQuery.data.value.size_bytes) }}
        </el-descriptions-item>
        <el-descriptions-item label="部门">
          {{ detailQuery.data.value.department ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="文档类型">
          {{ detailQuery.data.value.doc_type ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="版本">
          {{ detailQuery.data.value.version }}
        </el-descriptions-item>
        <el-descriptions-item label="页数">
          {{ detailQuery.data.value.page_count ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="元素数">
          {{ detailQuery.data.value.element_count ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="切片数">
          {{ detailQuery.data.value.chunk_count ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="租户">
          {{ detailQuery.data.value.tenant }}
        </el-descriptions-item>
        <el-descriptions-item label="文档标识">
          <span
            class="trace-id"
            data-testid="documents-copy-id"
          >{{ detailQuery.data.value.id }}</span>
          <el-button
            size="small"
            text
            type="primary"
            style="margin-left: 8px"
            @click="copyDocumentId(detailQuery.data.value.id)"
          >
            {{ idCopied ? '已复制' : '复制' }}
          </el-button>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 入库进度:按状态机呈现当前所在的那一段,不画百分比进度条 -->
      <el-divider content-position="left">
        入库进度
      </el-divider>
      <div
        v-if="progress.length > 0"
        class="progress-track"
        data-testid="documents-progress"
      >
        <div
          v-for="step in progress"
          :key="step.key"
          class="progress-step"
          :class="`progress-step--${step.state}`"
          :data-testid="`documents-progress-${step.key}`"
        >
          <span class="progress-step__dot" />
          <span class="progress-step__label">{{ step.label }}</span>
        </div>
      </div>
      <p
        v-else
        class="page-hint"
        data-testid="documents-progress"
      >
        {{ statusLabel(detailQuery.data.value.status) }}
      </p>

      <!-- 失败可恢复 -->
      <div
        v-if="detailQuery.data.value.status === 'failed'"
        class="failure-box"
      >
        <p
          class="page-hint"
          style="color: var(--el-color-danger)"
          data-testid="documents-error-reason"
        >
          失败原因:{{ detailQuery.data.value.error_message ?? '未提供失败原因' }}
        </p>
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
          <el-upload
            :auto-upload="false"
            :limit="1"
            :show-file-list="false"
            :accept="SUPPORTED_EXTENSIONS.map((ext) => `.${ext}`).join(',')"
            data-testid="documents-reupload-input"
            @change="onReuploadPicked($event.raw ?? null)"
          >
            <el-button
              size="small"
              plain
            >
              选择同一份知识库文档
            </el-button>
          </el-upload>
          <el-button
            type="primary"
            size="small"
            :loading="reuploadMutation.isPending.value"
            :disabled="!reuploadFile || Boolean(reuploadExtensionError)"
            data-testid="documents-reupload"
            @click="onReupload"
          >
            重新上传
          </el-button>
          <span class="page-hint">
            重新上传即后端定义的同标识重试语义(同租户 + 同文档名,旧版本退位)
          </span>
        </div>
        <p
          v-if="reuploadFile"
          class="page-hint"
          style="margin-top: 4px"
        >
          已选择:{{ reuploadFile.name }}
        </p>
        <p
          v-if="reuploadExtensionError"
          class="page-hint"
          style="color: var(--el-color-danger)"
          data-testid="documents-reupload-extension-error"
        >
          {{ reuploadExtensionError }}
        </p>
      </div>
      <ErrorDetails
        v-if="reuploadMutation.isError.value"
        :error="reuploadMutation.error.value"
      />

      <!--
        停在 chunked 的文档:可单独触发向量化,不必重传整份知识库文档
        (上游向量化失败的可重试落点,见后端的向量化端点)。
      -->
      <div
        v-if="detailQuery.data.value.status === 'chunked'"
        style="margin-top: 12px"
      >
        <el-button
          type="primary"
          :loading="embedMutation.isPending.value"
          data-testid="documents-retry-embedding"
          @click="embedMutation.mutate(detailQuery.data.value.id)"
        >
          重试向量化
        </el-button>
        <span
          class="page-hint"
          style="margin-left: 8px"
        >
          文档已切片,尚未入库;可在此单独触发向量化,不必重传整份知识库文档
        </span>
      </div>
      <ErrorDetails
        v-if="embedMutation.isError.value"
        :error="embedMutation.error.value"
      />

      <!-- 时间线:创建 / 解析 / 切片 / 索引 / 失败 / 退位 / 删除 -->
      <el-divider content-position="left">
        时间线
      </el-divider>
      <ul
        class="timeline"
        data-testid="documents-timeline"
      >
        <li
          v-for="entry in timeline"
          :key="entry.key"
          class="timeline__entry"
          :data-testid="`documents-timeline-${entry.key}`"
        >
          <span class="timeline__label">{{ entry.label }}</span>
          <span class="timeline__time">{{ formatTime(entry.at) }}</span>
        </li>
        <li
          v-if="timeline.length === 0"
          class="timeline__entry"
        >
          暂无时间线
        </li>
      </ul>

      <!-- 切片预览:确认切分粒度是否符合预期 -->
      <el-divider content-position="left">
        切片预览
      </el-divider>
      <div
        v-if="chunksQuery.data.value && chunksQuery.data.value.chunks.length > 0"
        data-testid="documents-chunks"
      >
        <el-collapse>
          <el-collapse-item
            v-for="chunk in chunksQuery.data.value.chunks"
            :key="chunk.chunk_id"
            :title="`#${chunk.index}${chunk.title ? ` · ${chunk.title}` : ''}${chunk.page_number ? ` · 第 ${chunk.page_number} 页` : ''}`"
          >
            <pre
              style="white-space: pre-wrap; margin: 0; font-size: 13px"
            >{{ chunk.content }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
      <p
        v-else
        class="page-hint"
        data-testid="documents-chunks"
      >
        暂无切片
      </p>

      <!-- 解析元素:按类型折叠分组 -->
      <el-divider content-position="left">
        解析元素
      </el-divider>
      <div
        v-if="elementGroups.length > 0"
        data-testid="documents-elements"
      >
        <el-collapse>
          <el-collapse-item
            v-for="group in elementGroups"
            :key="group.type"
            :name="group.type"
            :title="`${elementTypeLabel(group.type)}(${group.elements.length})`"
          >
            <div
              v-for="element in group.elements"
              :key="element.order_index"
              class="element-row"
            >
              <span class="element-row__meta">
                #{{ element.order_index }}{{ element.page_number ? ` · 第 ${element.page_number} 页` : '' }}
              </span>
              <pre
                style="white-space: pre-wrap; margin: 0; font-size: 13px"
              >{{ element.content }}</pre>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
      <p
        v-else
        class="page-hint"
        data-testid="documents-elements"
      >
        暂无解析元素
      </p>

      <!-- 操作 -->
      <el-divider content-position="left">
        操作
      </el-divider>
      <slot name="actions">
        <span class="page-hint">
          删除会连带清理该文档的全部切片与向量
        </span>
      </slot>
    </template>
    <ErrorDetails
      v-if="detailQuery.isError.value"
      :error="detailQuery.error.value"
    />
  </el-drawer>
</template>

<style scoped>
/**
 * 入库进度的状态机轨道。
 *
 * **刻意不用 el-progress**:状态机有分支与重试点,百分比是编造的。
 */
.progress-track {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
}

.progress-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.progress-step__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--el-border-color);
}

.progress-step--done {
  color: var(--el-text-color-primary);
}

.progress-step--done .progress-step__dot {
  background-color: var(--el-color-success);
}

.progress-step--current {
  color: var(--el-color-primary);
  font-weight: 600;
}

.progress-step--current .progress-step__dot {
  background-color: var(--el-color-primary);
}

.progress-step--failed {
  color: var(--el-color-danger);
  font-weight: 600;
}

.progress-step--failed .progress-step__dot {
  background-color: var(--el-color-danger);
}

/** 失败原因与恢复入口。 */
.failure-box {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-color-danger-light-7);
  border-radius: 4px;
  background-color: var(--el-color-danger-light-9);
}

/** 生命周期时间线。 */
.timeline {
  margin: 0;
  padding-left: 16px;
  list-style: none;
}

.timeline__entry {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  padding: 3px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.timeline__time {
  color: var(--el-text-color-secondary);
}

/** 解析元素条目。 */
.element-row {
  padding: 4px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.element-row__meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
