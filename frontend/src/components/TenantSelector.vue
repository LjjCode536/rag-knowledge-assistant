<script setup lang="ts">
/**
 * 全局租户选择器:可选、可新建,选择结果在页面间保持(Pinia)。
 *
 * 边界(ADR-0015):租户只是**筛选维度**,不是权限控制 —— 后端当前没有
 * 用户、会话与鉴权,任何调用方都能传任意 `tenant`。界面如实呈现这一现状。
 */
import { computed, ref } from 'vue'

import { useTenantStore } from '@/stores/tenant'

const tenantStore = useTenantStore()

/** 新建租户的输入。 */
const newTenant = ref('')

const selected = computed<string>({
  get: () => tenantStore.current,
  set: (value) => tenantStore.select(value),
})

function onCreate(): void {
  const value = newTenant.value.trim()
  if (!value) {
    return
  }
  tenantStore.create(value)
  newTenant.value = ''
}
</script>

<template>
  <div class="app-sidebar__tenant">
    <div style="margin-bottom: 8px; font-size: 13px; color: var(--el-text-color-secondary)">
      租户
    </div>
    <el-select
      v-model="selected"
      placeholder="全部租户"
      clearable
      size="small"
      style="width: 100%"
      aria-label="租户筛选"
    >
      <el-option
        label="全部租户"
        value=""
      />
      <el-option
        v-for="option in tenantStore.options"
        :key="option.value"
        :label="option.label"
        :value="option.value"
      />
    </el-select>
    <div style="display: flex; gap: 6px; margin-top: 8px">
      <el-input
        v-model="newTenant"
        size="small"
        placeholder="新建租户"
        aria-label="新建租户"
        @keyup.enter="onCreate"
      />
      <el-button
        size="small"
        type="primary"
        plain
        @click="onCreate"
      >
        新建
      </el-button>
    </div>
  </div>
</template>
