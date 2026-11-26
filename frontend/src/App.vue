<script setup lang="ts">
/**
 * 应用外壳:侧边导航 + 顶部标题 + 内容区,以及登录后的主体信息(ADR-0020)。
 *
 * 租户不再由界面选择:租户、角色与部门来自登录身份,只读展示。登录页不渲染
 * 应用外壳,避免未认证时看到导航。
 *
 * 窄屏下侧栏折叠为抽屉;内容区保持唯一滚动容器。
 */
import { computed, ref, watch } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useRoute, useRouter } from 'vue-router'

import { navItems } from '@/router'
import { useAuthStore } from '@/stores/auth'
import type { PrincipalRole } from '@/types/auth'

const MOBILE_BREAKPOINT = 768

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const ROLE_LABELS: Record<PrincipalRole, string> = {
  member: '成员',
  admin: '租户管理员',
  platform_operator: '平台管理员',
}

/** 当前主体的可读名称;主体尚未取回时留空,不伪造身份。 */
const principalName = computed<string>(
  () => authStore.principal?.display_name || authStore.principal?.username || '',
)

/** 当前主体的角色文案。 */
const roleLabel = computed<string>(() =>
  authStore.principal ? ROLE_LABELS[authStore.principal.role] : '',
)

/** 当前主体的租户与部门(只读展示,不提供切换)。 */
const scopeLabel = computed<string>(() => {
  const principal = authStore.principal
  if (!principal) {
    return ''
  }
  const department = principal.department ? ` · ${principal.department}` : ''
  return `租户 ${principal.tenant}${department}`
})

/** 登录页不渲染应用外壳。 */
const isLoginPage = computed<boolean>(() => route.name === 'login')

/** 退出登录:清本地凭证并回登录页(无服务端会话可注销)。 */
async function onSignOut(): Promise<void> {
  authStore.signOut()
  await router.push({ name: 'login' })
}

/** 窄屏:侧栏折叠为抽屉。 */
const isNarrow = ref(false)
/** 导航抽屉是否展开(窄屏)。 */
const drawerOpen = ref(false)

function syncViewport(): void {
  isNarrow.value = window.innerWidth < MOBILE_BREAKPOINT
}

syncViewport()
window.addEventListener('resize', syncViewport)

/** 当前页面标题:取路由 meta 上的标题。 */
const pageTitle = computed<string>(() => route.meta.title ?? '')

/** 路由切换后收起抽屉(窄屏下的导航应答)。 */
watch(
  () => route.path,
  () => {
    drawerOpen.value = false
  },
)
</script>

<template>
  <el-config-provider :locale="zhCn">
    <router-view v-if="isLoginPage" />

    <el-container
      v-else
      class="app-layout"
    >
      <!-- 宽屏:常驻侧栏 -->
      <el-aside
        v-if="!isNarrow"
        class="app-sidebar"
        width="220px"
      >
        <div class="app-sidebar__brand">
          企业 AI 知识库
        </div>
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

      <!-- 窄屏:抽屉式导航 -->
      <el-drawer
        v-model="drawerOpen"
        direction="ltr"
        size="220px"
        title="企业 AI 知识库"
      >
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
              导航
            </el-button>
            <span class="app-header__title">{{ pageTitle }}</span>
          </div>
          <div
            class="app-header__identity"
            data-testid="app-identity"
          >
            <span
              v-if="principalName"
              class="app-header__identity-name"
            >{{ principalName }}</span>
            <span
              v-if="scopeLabel"
              class="app-header__identity-scope"
            >{{ roleLabel }} · {{ scopeLabel }}</span>
            <el-button
              size="small"
              text
              data-testid="app-sign-out"
              @click="onSignOut"
            >
              退出登录
            </el-button>
          </div>
        </el-header>
        <el-main class="app-content">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </el-config-provider>
</template>
