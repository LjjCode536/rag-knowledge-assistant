<script setup lang="ts">
/**
 * 知识库文档页:上传 + 入库进度 + 文档清单。
 *
 * **详情抽屉独立成组件**(`components/DocumentDetailDrawer.vue`):抽屉关心的是
 * "这一份文档怎么样"(进度 / 时间线 / 切片 / 解析元素 / 恢复),本页关心的是
 * "有哪些文档"(筛选 / 分页 / 删除)。合在一起就是一个什么都要改的大文件。
 *
 * 已知契约陷阱(ADR-0015):
 * - **上传响应是任务句柄**:status 恒为 `uploaded`,入库进度须轮询档案查询;
 * - **切片失败不返回错误码**:入库失败在响应体里(`status == "failed"` +
 *   `error_message`),界面必须检查文档状态而非只看响应码。
 *
 * 术语遵循 `GLOSSARY.md`:知识库文档 / 入库进度 / 切片 / 解析元素;
 * 不用 `_Avoid_` 清单里的同义词(文件 / 片段 / 上传进度)。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'

import { deleteDocument, listDocuments, uploadDocument } from '@/api'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import ErrorDetails from '@/components/ErrorDetails.vue'
import {
  STATUS_LABELS,
  SUPPORTED_EXTENSIONS,
  extensionOf,
  isSupportedExtension,
  statusLabel,
  statusTagType,
} from '@/components/documentLogic'
import { useDebounced } from '@/composables/useDebounced'
import { useTenantStore } from '@/stores/tenant'
import type { DocumentStatus, DocumentView } from '@/types/documents'

const tenantStore = useTenantStore()
const queryClient = useQueryClient()

/** 关键字输入框的值(用户即时输入)。 */
const keywordInput = ref('')
/** 已防抖的关键字:停顿后才拿去查,避免每敲一个字打一次后端。 */
const keyword = useDebounced(keywordInput)
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

const uploadMutation = useMutation({
  mutationFn: (file: File) =>
    uploadDocument(file, {
      tenant: tenantStore.filter ?? 'default',
      department: uploadDepartment.value || undefined,
      doc_type: uploadDocType.value || undefined,
      version: uploadVersion.value || undefined,
    }),
  onSuccess: (uploaded) => {
    // 上传后立刻把抽屉指向新档案:入库进度在这里看得到
    detailId.value = uploaded.id
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
    void queryClient.invalidateQueries({ queryKey: ['document'] })
  },
})

/** 等待确认删除的文档 id(非空即表示确认框已弹出)。 */
const deleteTarget = ref<string | null>(null)

/**
 * 待删除文档的档案(确认框里要说清会连带清掉什么)。
 *
 * 抽屉里的删除入口可能指向**不在当前页**的文档(翻过页、或清单只列了
 * 现行版本),所以清单里找不到时回退到抽屉正在看的那份 —— 否则确认框会说
 * "该知识库文档",没告诉用户删的是哪一份。
 */
const deleteTargetDocument = computed<DocumentView | null>(
  () => documents.value.find((item) => item.id === deleteTarget.value) ?? null,
)

function onAskDelete(documentId: string): void {
  deleteTarget.value = documentId
}

function onCancelDelete(): void {
  deleteTarget.value = null
}

/**
 * 确认删除:删除是幂等的,重复删除不报错。
 *
 * 失败时**不关确认框** —— 清理失败(`DOCUMENT_DELETE_FAILED`)是可重试的,
 * 把错误露出来让用户看见,而不是让文档"人间蒸发"。
 */
function onConfirmDelete(): void {
  const id = deleteTarget.value
  if (!id) {
    return
  }
  deleteMutation.mutate(id, {
    onSuccess: () => {
      deleteTarget.value = null
    },
  })
}

/** 状态筛选下拉的选项:用状态标签的规范术语做文案(标签表收在 documentLogic)。 */
const statusOptions = computed(() =>
  (Object.keys(STATUS_LABELS) as DocumentStatus[]).map((value) => ({
    value,
    label: STATUS_LABELS[value],
  })),
)

/** 时刻的可读呈现。 */
function formatTime(value: string): string {
  return new Date(value).toLocaleString()
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

    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>文档清单</strong>
      </template>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px">
        <el-input
          v-model="keywordInput"
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
        <!--
          租户筛选用**全局租户选择器**的值(不另设一个):后端当前没有用户、
          会话与鉴权,租户只是一个筛选维度(ADR-0015)。另设一个输入框会
          造出"两套租户真相"。
        -->
        <el-tag
          size="small"
          type="info"
          data-testid="documents-tenant-filter"
        >
          租户:{{ tenantStore.currentLabel }}
        </el-tag>
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
        @size-change="page = 1"
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
      <ErrorDetails
        v-if="deleteMutation.isError.value"
        :error="deleteMutation.error.value"
      />
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

    <DocumentDetailDrawer
      :document-id="detailId"
      @close="detailId = null"
      @reuploaded="detailId = $event"
    >
      <template #actions>
        <el-button
          type="danger"
          plain
          data-testid="documents-delete-in-drawer"
          :disabled="!detailId"
          @click="onAskDelete(detailId!)"
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
    </DocumentDetailDrawer>
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
</style>
