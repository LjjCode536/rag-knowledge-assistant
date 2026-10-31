<script setup lang="ts">
/**
 * 检索页:独立的检索面板,暴露 top_k 与全部过滤维度。
 *
 * 过滤维度与后端 `ChunkFilter` 同名(租户 / 部门 / 文档类型 / 版本 /
 * 文档 id);**过滤发生在检索之前**,命中里不应出现被过滤掉的切片。
 */
import { useMutation } from '@tanstack/vue-query'
import { computed, ref } from 'vue'

import { search } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
import { useTenantStore } from '@/stores/tenant'
import type { SearchHit } from '@/types/qa'

const tenantStore = useTenantStore()

const query = ref('')
const topK = ref(5)
const department = ref('')
const docType = ref('')
const version = ref('')
const documentId = ref('')

const searchMutation = useMutation({
  mutationFn: () =>
    search(query.value.trim(), {
      top_k: topK.value,
      filters: {
        ...(tenantStore.filter ? { tenant: tenantStore.filter } : {}),
        ...(department.value ? { department: department.value } : {}),
        ...(docType.value ? { doc_type: docType.value } : {}),
        ...(version.value ? { version: version.value } : {}),
        ...(documentId.value ? { document_id: documentId.value } : {}),
      },
    }),
})

const hits = computed(() => searchMutation.data.value?.hits ?? [])
const effectiveFilters = computed(() => searchMutation.data.value?.filters ?? {})

/** 表格行类型:模板插槽里不便写类型标注,在此声明一次。 */
type HitRow = SearchHit
</script>

<template>
  <div>
    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>检索</strong>
      </template>
      <el-form
        label-width="90px"
        @submit.prevent="searchMutation.mutate()"
      >
        <el-form-item label="查询">
          <el-input
            v-model="query"
            placeholder="要检索的内容"
            data-testid="search-query"
            @keyup.enter="searchMutation.mutate()"
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
              v-model="department"
              size="small"
              placeholder="部门"
              style="max-width: 130px"
            />
            <el-input
              v-model="docType"
              size="small"
              placeholder="文档类型"
              style="max-width: 130px"
            />
            <el-input
              v-model="version"
              size="small"
              placeholder="版本"
              style="max-width: 110px"
            />
            <el-input
              v-model="documentId"
              size="small"
              placeholder="文档 id"
              style="max-width: 200px"
            />
          </div>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="searchMutation.isPending.value"
            data-testid="search-submit"
            @click="searchMutation.mutate()"
          >
            检索
          </el-button>
        </el-form-item>
      </el-form>
      <p class="page-hint">
        当前租户:<strong>{{ tenantStore.currentLabel }}</strong>。过滤发生在检索之前,被过滤掉的切片不参与打分与排名。
      </p>
    </el-card>

    <ErrorDetails
      v-if="searchMutation.isError.value"
      :error="searchMutation.error.value"
    />

    <el-card
      v-if="searchMutation.data.value"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
          <strong>命中</strong>
          <span class="page-hint">
            共 {{ searchMutation.data.value.total }} 条 · 生效过滤:
            {{ Object.keys(effectiveFilters).length ? JSON.stringify(effectiveFilters) : '无' }}
          </span>
        </div>
      </template>
      <el-table
        :data="hits"
        style="width: 100%"
      >
        <el-table-column
          label="分数"
          width="90"
        >
          <template #default="{ row }: { row: HitRow }">
            {{ row.score }}
          </template>
        </el-table-column>
        <el-table-column
          prop="chunk.content"
          label="切片内容"
          min-width="320"
        />
        <el-table-column
          prop="chunk.tenant"
          label="租户"
          width="110"
        />
        <el-table-column
          label="标题"
          width="140"
        >
          <template #default="{ row }: { row: HitRow }">
            {{ row.chunk.title ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="页码"
          width="80"
        >
          <template #default="{ row }: { row: HitRow }">
            {{ row.chunk.page_number ?? '—' }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
