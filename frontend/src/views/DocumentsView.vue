<script setup lang="ts">
/**
 * 知识库文档页:上传 + 文档列表 + 详情抽屉。
 *
 * 已知契约陷阱(ADR-0015):
 * - **上传响应是任务句柄**:status 恒为 `uploaded`,入库进度须轮询档案查询;
 * - **切片失败不返回错误码**:入库失败在响应体里(`status == "failed"` +
 *   `error_message`),界面必须检查文档状态而非只看响应码。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'

import { deleteDocument, getDocument, listDocuments, uploadDocument } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
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

/** 上传表单。 */
const uploadFile = ref<File | null>(null)
const uploadDepartment = ref('')
const uploadDocType = ref('')
const uploadVersion = ref('v1')

/** 支持的扩展名:上传前就校验,不让用户白等一次上传失败。 */
const SUPPORTED_EXTENSIONS = ['txt', 'md', 'markdown', 'html', 'htm', 'docx', 'pdf']

/** 前端选文件时就拦下不受支持的扩展名。 */
const extensionError = computed<string>(() => {
  const file = uploadFile.value
  if (!file) {
    return ''
  }
  const suffix = file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : ''
  return SUPPORTED_EXTENSIONS.includes(suffix) ? '' : `不支持的文件扩展名:.${suffix || '(无)'}`
})

function onFileChange(file: { raw?: File } | undefined): void {
  uploadFile.value = file?.raw ?? null
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

/** 状态标签的呈现:状态 → 标签类型。 */
const STATUS_LABELS: Record<DocumentStatus, string> = {
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

function statusLabel(status: DocumentStatus): string {
  return STATUS_LABELS[status] ?? status
}

function statusTagType(status: DocumentStatus): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'indexed') {
    return 'success'
  }
  if (status === 'failed') {
    return 'danger'
  }
  if (status === 'superseded' || status === 'deleted') {
    return 'info'
  }
  return 'warning'
}

/** 入库进度:文档处于进行中的中间态。 */
const IN_PROGRESS: DocumentStatus[] = [
  'uploaded',
  'parsing',
  'parsed',
  'chunking',
  'chunked',
  'embedding',
]

function isInProgress(status: DocumentStatus): boolean {
  return IN_PROGRESS.includes(status)
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
      <el-form
        label-width="90px"
        @submit.prevent="onUpload"
      >
        <el-form-item label="文件">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".txt,.md,.markdown,.html,.htm,.docx,.pdf"
            :on-change="onFileChange"
            :on-remove="() => (uploadFile = null)"
          >
            <el-button>选择文件</el-button>
          </el-upload>
          <div
            v-if="uploadFile"
            class="page-hint"
            style="margin-top: 4px"
          >
            {{ uploadFile.name }}
          </div>
          <div
            v-if="extensionError"
            class="page-hint"
            style="color: var(--el-color-danger)"
          >
            {{ extensionError }}
          </div>
        </el-form-item>
        <el-form-item label="租户">
          <span>{{ tenantStore.currentLabel }}</span>
        </el-form-item>
        <el-form-item label="高级设置">
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <el-input
              v-model="uploadDepartment"
              size="small"
              placeholder="部门(可选)"
            />
            <el-input
              v-model="uploadDocType"
              size="small"
              placeholder="文档类型(可选)"
            />
            <el-input
              v-model="uploadVersion"
              size="small"
              placeholder="版本"
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
        上传立即返回任务句柄,入库进度在详情里轮询查看。支持的格式:txt / md / html / docx / pdf。
      </p>
    </el-card>

    <ErrorDetails
      v-if="uploadMutation.isError.value"
      :error="uploadMutation.error.value"
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
          placeholder="文件名关键字"
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
        >
          <el-option
            v-for="(label, value) in STATUS_LABELS"
            :key="value"
            :label="label"
            :value="value"
          />
        </el-select>
        <el-checkbox v-model="includeHistory">
          含历史版本
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
          label="文件名"
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
            {{ new Date(row.created_at).toLocaleString() }}
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
              @click="detailId = row.id"
            >
              详情
            </el-button>
            <el-button
              size="small"
              text
              type="danger"
              @click="deleteMutation.mutate(row.id)"
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

    <el-drawer
      :model-value="Boolean(detailId)"
      title="知识库文档详情"
      size="520px"
      @close="detailId = null"
    >
      <template v-if="detailQuery.data.value">
        <el-descriptions
          :column="1"
          border
        >
          <el-descriptions-item label="文件名">
            {{ detailQuery.data.value.file_name }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag
              :type="statusTagType(detailQuery.data.value.status)"
              size="small"
            >
              {{ statusLabel(detailQuery.data.value.status) }}
            </el-tag>
            <span
              v-if="isInProgress(detailQuery.data.value.status)"
              class="page-hint"
            >
              (入库进行中)
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="租户">
            {{ detailQuery.data.value.tenant }}
          </el-descriptions-item>
          <el-descriptions-item label="切片数">
            {{ detailQuery.data.value.chunk_count ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="文档 id">
            <span class="trace-id">{{ detailQuery.data.value.id }}</span>
          </el-descriptions-item>
          <el-descriptions-item
            v-if="detailQuery.data.value.error_message"
            label="失败原因"
          >
            <span style="color: var(--el-color-danger)">
              {{ detailQuery.data.value.error_message }}
            </span>
          </el-descriptions-item>
        </el-descriptions>
      </template>
      <ErrorDetails
        v-if="detailQuery.isError.value"
        :error="detailQuery.error.value"
      />
    </el-drawer>
  </div>
</template>
