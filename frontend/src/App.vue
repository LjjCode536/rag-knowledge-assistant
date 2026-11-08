<script setup lang="ts">
/**
 * 应用外壳:左侧边栏导航 + 顶部标题栏 + 内容区。
 *
 * 骨架阶段的取舍(issue #20):
 * - 全局租户选择器在骨架阶段就立起来(数据源后接),选择结果在页面间保持;
 * - 窄屏下侧边栏折叠为抽屉,主要内容区不横向溢出;
 * - 界面文案遵循 `GLOSSARY.md` 的规范术语(知识库文档 / 切片 / 引用 /
 *   查询日志 / 拒答 / 入库进度),不使用其 `_Avoid_` 清单里的同义词。
 */
import { computed, ref, watch } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useRoute } from 'vue-router'

import TenantSelector from '@/components/TenantSelector.vue'
import { useTenantOptions } from '@/composables/useTenantOptions'
import { navItems } from '@/router'

const MOBILE_BREAKPOINT = 768

const route = useRoute()

// 租户选项取自后端文档清单的真实租户维度(应用级,不挂在某个页面上)
useTenantOptions()

/** 窄屏:侧边栏折叠为抽屉。 */
const isNarrow = ref(false)
/** 抽屉是否展开(窄屏下)。 */
const drawerOpen = ref(false)

function syncViewport(): void {
  isNarrow.value = window.innerWidth < MOBILE_BREAKPOINT
}

syncViewport()
window.addEventListener('resize', syncViewport)

/** 当前页标题:取路由 meta 上的标题。 */
const pageTitle = computed<string>(() => {
  const title = route.meta.title
  return typeof title === 'string' ? title : ''
})

/** 路由切换后收起抽屉(窄屏下导航完应回到内容区)。 */
watch(
  () => route.path,
  () => {
    drawerOpen.value = false
  },
)
</script>

<template>
  <el-config-provider :locale="zhCn">
    <el-container class="app-layout">
      <!-- 宽屏:常驻侧边栏 -->
      <el-aside
        v-if="!isNarrow"
        class="app-sidebar"
        width="220px"
      >
        <div class="app-sidebar__brand">
          企业 AI 知识库
        </div>
        <TenantSelector />
        <el-menu
          :default-active="route.path"
          router
          class="app-sidebar__menu"
        >
          <el-menu-item
            v-for="item in navItems"
            :key="item.path"
            :index="item.path"
          >
            {{ item.title }}
          </el-menu-item>
        </el-menu>
      </el-aside>

      <!-- 窄屏:抽屉式侧边栏 -->
      <el-drawer
        v-model="drawerOpen"
        direction="ltr"
        size="220px"
        title="企业 AI 知识库"
      >
        <TenantSelector />
        <el-menu
          :default-active="route.path"
          router
        >
          <el-menu-item
            v-for="item in navItems"
            :key="item.path"
            :index="item.path"
          >
            {{ item.title }}
          </el-menu-item>
        </el-menu>
      </el-drawer>

      <el-container class="app-main">
        <el-header class="app-header">
          <div style="display: flex; align-items: center; gap: 12px">
            <el-button
              v-if="isNarrow"
              text
              aria-label="打开导航"
              @click="drawerOpen = true"
            >
              菜单
            </el-button>
            <span class="app-header__title">{{ pageTitle }}</span>
          </div>
        </el-header>
        <el-main class="app-content">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </el-config-provider>
</template>
