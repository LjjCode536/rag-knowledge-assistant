<script setup lang="ts">
/**
 * 知识库文档页:上传 + 入库进度 + 文档清单 + 详情抽屉。
 *
 * 已知契约陷阱(ADR-0015):
 * - **上传响应是任务句柄**:status 恒为 `uploaded`,入库进度须轮询档案查询;
 * - **切片失败不返回错误码**:入库失败在响应体里(`status == "failed"` +
 *   `error_message`),界面必须检查文档状态而非只看响应码;
 * - **重试语义分两路**:解析 / 切片失败落 `failed`(终态),只能重新上传;
 *   向量化失败停 `chunked`,可单独重试向量化,不必重传整份知识库文档。
 *
 * **不画百分比进度条**:入库进度按状态机推进,状态机有分支(失败)与重试点
 * (重试向量化),编造百分比是撒谎。界面按"当前所在的那一段"呈现。
 *
 * 术语遵循 `GLOSSARY.md`:知识库文档 / 入库进度 / 切片 / 解析元素;
 * 不用 `_Avoid_` 清单里的同义词(文件 / 片段 / 上传进度)。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'

import {
  deleteDocument,
  getDocument,
  getDocumentElements,
  listChunks,
  listDocuments,
  retryEmbedding,
  uploadDocument,
} from '@/api'
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
import { useTenantStore } from '@/stores/tenant'
import type { DocumentStatus, DocumentView } from '@/types/documents'

const tenantStore = useTenantStore()
const queryClient = useQueryClient()

/** 列表筛选条件。 */
const keyword = ref('')
const statusFilter = ref<DocumentStatus | ''>('')
const includeHistory = ref(false)
const page = ref(1)
const pageSize = ref(20)

/** 打开详情抽屉的文档 id。 */
const detailId = ref<string | null>(null)

const listQuery = useQuery({
  queryKey: computed(
    () =>
      [
        'documents',
        {
          tenant: tenantStore.filter,
          keyword: keyword.value,
          status: statusFilter.value,
          includeHistory: includeHistory.value,
          page: page.value,
          pageSize: pageSize.value,
        },
      ] as const,
  ),
  queryFn: () =>
    listDocuments({
      tenant: tenantStore.filter,
      keyword: keyword.value || undefined,
      status: statusFilter.value || undefined,
      include_history: includeHistory.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    }),
})

const documents = computed(() => listQuery.data.value?.items ?? [])
const total = computed(() => listQuery.data.value?.total ?? 0)

/** 详情抽屉的数据:按 id 查询单份档案。 */
const detailQuery = useQuery({
  queryKey: computed(() => ['document', detailId.value] as const),
  queryFn: () => getDocument(detailId.value!),
  enabled: computed(() => Boolean(detailId.value)),
  // 详情抽屉开着时持续轮询,以便看到入库进度推进
  refetchInterval: computed(() => (detailId.value ? 2000 : false)),
})

/** 切片预览:抽屉打开且文档已切片时取全量切片。 */
const chunksQuery = useQuery({
  queryKey: computed(() => ['document', detailId.value, 'chunks'] as const),
  queryFn: () => listChunks(detailId.value!),
  enabled: computed(() => Boolean(detailId.value)),
})

/** 解析元素:抽屉打开时取结构化解析产物。 */
const elementsQuery = useQuery({
  queryKey: computed(() => ['document', detailId.value, 'elements'] as const),
  queryFn: () => getDocumentElements(detailId.value!),
  enabled: computed(() => Boolean(detailId.value)),
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

/** 上传表单。 */
const uploadFile = ref<File | null>(null)
const uploadDepartment = ref('')
const uploadDocType = ref('')
const uploadVersion = ref('v1')

/** 高级设置是否展开(默认收起:多数上传不需要填)。 */
const advancedOpen = ref(false)

/**
 * 扩展名是否受支持。
 *
 * **在选择文件时就校验**:不让用户白等一次必然失败的上传。
 */
const extensionSupported = computed(() =>
  uploadFile.value ? isSupportedExtension(uploadFile.value.name) : true,
)

/** 扩展名不受支持时的提示(界面文案用规范术语:知识库文档)。 */
const extensionError = computed<string>(() => {
  const file = uploadFile.value
  if (!file || extensionSupported.value) {
    return ''
  }
  const suffix = extensionOf(file.name)
  return `不支持的知识库文档格式:.${suffix || '(无扩展名)'};支持的格式:txt / md / html / docx / pdf`
})

/**
 * 重新上传:后端定义的同标识重试语义(同租户 + 同文档名 → 旧版本退位)。
 *
 * **失败原因在后端,重传内容在用户手上**:后端没有"取回原始文档"的端点,
 * 所以重新上传必须由用户重新挑一次同一份知识库文档 —— 界面据此在失败
 * 区块里放一个文件选择入口,而不是假设浏览器还留着上次的文件。
 */
const reuploadFile = ref<File | null>(null)

/** 重新上传的目标档案 id。 */
const reuploadTargetId = ref<string | null>(null)

/** 重新上传所选文件的扩展名是否受支持。 */
const reuploadExtensionError = computed<string>(() => {
  const file = reuploadFile.value
  if (!file || isSupportedExtension(file.name)) {
    return ''
  }
  return `不支持的知识库文档格式:.${extensionOf(file.name) || '(无扩展名)'}`
})

const reuploadMutation = useMutation({
  mutationFn: (file: File) =>
    uploadDocument(file, {
      tenant: tenantStore.filter ?? 'default',
      department: uploadDepartment.value || undefined,
      doc_type: uploadDocType.value || undefined,
      version: uploadVersion.value || undefined,
    }),
  onSuccess: () => {
    reuploadFile.value = null
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
    void queryClient.invalidateQueries({ queryKey: ['document', reuploadTargetId.value] })
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

const uploadMutation = useMutation({
  mutationFn: (file: File) =>
    uploadDocument(file, {
      tenant: tenantStore.filter ?? 'default',
      department: uploadDepartment.value || undefined,
      doc_type: uploadDocType.value || undefined,
      version: uploadVersion.value || undefined,
    }),
  onSuccess: () => {
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
  },
})

/**
 * 选中文件。
 *
 * 拖拽与点击选择走同一条路径 —— 两条路径都必须在**选中时**校验扩展名。
 */
function onFilePicked(file: File | null): void {
  uploadFile.value = file
}

/** 拖拽落选:取第一个落选的知识库文档。 */
function onDrop(event: DragEvent): void {
  const files = event.dataTransfer?.files
  if (!files || files.length === 0) {
    return
  }
  onFilePicked(files[0])
}

/** 拖拽经过上传区时的视觉反馈。 */
const dragActive = ref(false)

function onDragOver(): void {
  dragActive.value = true
}

function onDragLeave(): void {
  dragActive.value = false
}

function onUpload(): void {
  const file = uploadFile.value
  if (!file || extensionError.value) {
    return
  }
  uploadMutation.mutate(file)
}

const deleteMutation = useMutation({
  mutationFn: (documentId: string) => deleteDocument(documentId),
  onSuccess: () => {
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
  },
})

/** 等待确认删除的文档 id(非空即表示确认框已弹出)。 */
const deleteTarget = ref<string | null>(null)

/** 待删除文档的档案(确认框里要说清会连带清掉什么)。 */
const deleteTargetDocument = computed<DocumentView | null>(
  () => documents.value.find((item) => item.id === deleteTarget.value) ?? null,
)

function onAskDelete(documentId: string): void {
  deleteTarget.value = documentId
}

function onCancelDelete(): void {
  deleteTarget.value = null
}

/** 确认删除:删除是幂等的,重复删除不报错。 */
function onConfirmDelete(): void {
  const id = deleteTarget.value
  if (!id) {
    return
  }
  deleteMutation.mutate(id)
  deleteTarget.value = null
}

/** 重试向量化:文档停在 `chunked` 时可单独重试,不必重传整份知识库文档。 */
const embedMutation = useMutation({
  mutationFn: (documentId: string) => retryEmbedding(documentId),
  onSuccess: () => {
    void queryClient.invalidateQueries({ queryKey: ['document', detailId.value] })
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
  },
})

/** 状态筛选下拉的选项:用状态标签的规范术语做文案。 */
const statusOptions = computed(() =>
  (Object.keys(statusLabels) as DocumentStatus[]).map((value) => ({
    value,
    label: statusLabels[value],
  })),
)

/** 状态标签的呈现:状态 → 标签类型。 */
const statusLabels: Record<DocumentStatus, string> = {
  uploaded: '已登记',
  parsing: '解析中',
  parsed: '已解析',
  chunking: '切片中',
  chunked: '已切片',
  embedding: '向量化中',
  indexed: '已入库',
  failed: '失败',
  superseded: '已退位',
  deleted: '已删除',
}

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
  <div>
    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>上传知识库文档</strong>
      </template>

      <!--
        拖拽上传区:主区是拖拽落选,点击亦可选文件。
        两条路径都在**选中时**校验扩展名(见 onFilePicked)。
      -->
      <div
        class="upload-dropzone"
        :class="{ 'upload-dropzone--active': dragActive }"
        data-testid="documents-dropzone"
        @drop.prevent="onDrop"
        @dragover.prevent="onDragOver"
        @dragleave.prevent="onDragLeave"
      >
        <p class="upload-dropzone__hint">
          把知识库文档拖到这里,或
          <el-upload
            :auto-upload="false"
            :limit="1"
            :accept="SUPPORTED_EXTENSIONS.map((ext) => `.${ext}`).join(',')"
            :show-file-list="false"
            data-testid="documents-file-input"
            @change="onFilePicked($event.raw ?? null)"
          >
            <el-button
              size="small"
              type="primary"
              plain
            >
              选择知识库文档
            </el-button>
          </el-upload>
        </p>
        <p class="page-hint">
          支持的格式:txt / md / html / docx / pdf(markdown 与 htm 为别名)。
        </p>
        <div
          v-if="uploadFile"
          class="page-hint"
          style="margin-top: 4px"
        >
          已选择:{{ uploadFile.name }}
        </div>
        <div
          v-if="extensionError"
          class="page-hint"
          style="color: var(--el-color-danger)"
          data-testid="documents-extension-error"
        >
          {{ extensionError }}
        </div>
      </div>

      <el-form
        label-width="90px"
        style="margin-top: 12px"
        @submit.prevent="onUpload"
      >
        <el-form-item label="租户">
          <span>{{ tenantStore.currentLabel }}</span>
        </el-form-item>

        <el-form-item label="高级设置">
          <el-button
            size="small"
            text
            type="primary"
            data-testid="documents-advanced-toggle"
            @click="advancedOpen = !advancedOpen"
          >
            {{ advancedOpen ? '收起' : '展开' }}(部门 / 文档类型 / 版本)
          </el-button>
        </el-form-item>
        <el-form-item
          v-if="advancedOpen"
          label-width="90px"
        >
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <el-input
              v-model="uploadDepartment"
              size="small"
              placeholder="部门(可选)"
              data-testid="documents-department"
            />
            <el-input
              v-model="uploadDocType"
              size="small"
              placeholder="文档类型(可选)"
              data-testid="documents-doctype"
            />
            <el-input
              v-model="uploadVersion"
              size="small"
              placeholder="版本"
              data-testid="documents-version"
            />
          </div>
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            :loading="uploadMutation.isPending.value"
            :disabled="!uploadFile || Boolean(extensionError)"
            data-testid="documents-upload"
            @click="onUpload"
          >
            上传
          </el-button>
        </el-form-item>
      </el-form>
      <p class="page-hint">
        上传立即返回任务句柄,入库进度在详情里轮询查看 —— 界面按状态机呈现当前所在的那一段,不显示百分比进度条。
      </p>
    </el-card>

    <ErrorDetails
      v-if="uploadMutation.isError.value"
      :error="uploadMutation.error.value"
    />
    <ErrorDetails
      v-if="reuploadMutation.isError.value"
      :error="reuploadMutation.error.value"
    />

    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>文档清单</strong>
      </template>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px">
        <el-input
          v-model="keyword"
          size="small"
          placeholder="文档名关键字"
          style="max-width: 200px"
          data-testid="documents-keyword"
          @keyup.enter="((page = 1), listQuery.refetch())"
        />
        <el-select
          v-model="statusFilter"
          size="small"
          placeholder="状态"
          clearable
          style="max-width: 140px"
          data-testid="documents-status-filter"
        >
          <el-option
            v-for="option in statusOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-checkbox
          v-model="includeHistory"
          data-testid="documents-include-history"
        >
          含历史版本与已删除文档
        </el-checkbox>
        <el-button
          size="small"
          @click="((page = 1), listQuery.refetch())"
        >
          筛选
        </el-button>
      </div>

      <ErrorDetails
        v-if="listQuery.isError.value"
        :error="listQuery.error.value"
      />

      <el-table
        v-loading="listQuery.isPending.value"
        :data="documents"
        style="width: 100%"
      >
        <el-table-column
          prop="file_name"
          label="文档名"
          min-width="200"
        />
        <el-table-column
          label="状态"
          width="120"
        >
          <template #default="{ row }: { row: DocumentView }">
            <el-tag
              :type="statusTagType(row.status)"
              size="small"
            >
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="tenant"
          label="租户"
          width="120"
        />
        <el-table-column
          label="切片数"
          width="90"
        >
          <template #default="{ row }: { row: DocumentView }">
            {{ row.chunk_count ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="创建时间"
          width="180"
        >
          <template #default="{ row }: { row: DocumentView }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="150"
        >
          <template #default="{ row }: { row: DocumentView }">
            <el-button
              size="small"
              text
              type="primary"
              :data-testid="`documents-detail-${row.id}`"
              @click="detailId = row.id"
            >
              详情
            </el-button>
            <el-button
              size="small"
              text
              type="danger"
              :data-testid="`documents-delete-${row.id}`"
              @click="onAskDelete(row.id)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        style="margin-top: 12px"
      />
    </el-card>

    <el-dialog
      :model-value="Boolean(deleteTarget)"
      title="删除知识库文档"
      width="440px"
      @close="onCancelDelete"
    >
      <p data-testid="documents-delete-confirm">
        删除「{{ deleteTargetDocument?.file_name ?? '该知识库文档' }}」会连带清理它的
        <strong>全部切片与向量</strong>,文档档案保留在「已删除」终态可供审计。
        删除是幂等的,重复删除不报错。
      </p>
      <template #footer>
        <el-button
          @click="onCancelDelete"
        >
          取消
        </el-button>
        <el-button
          type="danger"
          data-testid="documents-delete-submit"
          @click="onConfirmDelete"
        >
          确认删除
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      :model-value="Boolean(detailId)"
      title="知识库文档详情"
      size="560px"
      @close="detailId = null"
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
            <span
              class="page-hint"
            >
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

        <!--
          停在 chunked 的文档:上游向量化失败后停在这里,可单独重试,
          不必重传整份知识库文档(后端的向量化端点)。
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
            上次向量化未完成,文档已切片,重试即可入库
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
        <el-button
          type="danger"
          plain
          data-testid="documents-delete-in-drawer"
          @click="onAskDelete(detailQuery.data.value.id)"
        >
          删除该知识库文档
        </el-button>
        <span
          class="page-hint"
          style="margin-left: 8px"
        >
          删除会连带清理该文档的全部切片与向量
        </span>
      </template>
      <ErrorDetails
        v-if="detailQuery.isError.value"
        :error="detailQuery.error.value"
      />
    </el-drawer>
  </div>
</template>

<style scoped>
/** 拖拽上传区:主区即落选区。 */
.upload-dropzone {
  border: 1px dashed var(--el-border-color);
  border-radius: 4px;
  padding: 20px;
  text-align: center;
  transition: border-color 0.2s, background-color 0.2s;
}

.upload-dropzone--active {
  border-color: var(--el-color-primary);
  background-color: var(--el-color-primary-light-9);
}

.upload-dropzone__hint {
  margin: 0 0 6px;
  font-size: 14px;
}

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
