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
import { describeEffectiveFilters } from '@/components/filterDimensions'
import { useFilterDimensions } from '@/composables/useFilterDimensions'
import type { SearchHit } from '@/types/qa'

const query = ref('')
const topK = ref(5)
/** 过滤维度:部门 / 文档类型 / 版本 / 文档 id(与问答页同一套口径)。 */
const { dimensions, filters } = useFilterDimensions()

const searchMutation = useMutation({
  mutationFn: () =>
    search(query.value.trim(), {
      top_k: topK.value,
      filters: filters.value,
    }),
})

const hits = computed(() => searchMutation.data.value?.hits ?? [])
const effectiveFilters = computed(() => searchMutation.data.value?.filters ?? {})

/** 生效过滤维度的文案:用规范术语而不是后端字段名。 */
const effectiveFilterText = computed<string>(() => describeEffectiveFilters(effectiveFilters.value))

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
              v-model="dimensions.department"
              size="small"
              placeholder="部门"
              style="max-width: 130px"
              data-testid="search-filter-department"
            />
            <el-input
              v-model="dimensions.docType"
              size="small"
              placeholder="文档类型"
              style="max-width: 130px"
              data-testid="search-filter-doc-type"
            />
            <el-input
              v-model="dimensions.version"
              size="small"
              placeholder="版本"
              style="max-width: 110px"
              data-testid="search-filter-version"
            />
            <el-input
              v-model="dimensions.documentId"
              size="small"
              placeholder="文档 id"
              style="max-width: 200px"
              data-testid="search-filter-document-id"
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
        租户来自登录身份;过滤发生在检索之前,被过滤掉的切片不参与打分与排名。
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
            {{ Object.keys(effectiveFilters).length ? effectiveFilterText : '无' }}
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
          label="部门"
          width="110"
        >
          <template #default="{ row }: { row: HitRow }">
            {{ row.chunk.department ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="文档类型"
          width="110"
        >
          <template #default="{ row }: { row: HitRow }">
            {{ row.chunk.doc_type ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="版本"
          width="90"
        >
          <template #default="{ row }: { row: HitRow }">
            {{ row.chunk.version }}
          </template>
        </el-table-column>
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
