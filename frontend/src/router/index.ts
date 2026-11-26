/**
 * 路由:六个页面各有独立路由,问答是默认首页(ADR-0015)。
 *
 * 刷新后路由保持(vue-router 的 history 模式 + HTML5 history fallback)。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** 页面标题(应用头部展示)。 */
    title?: string
    /** 公开路由:无需登录(登录页本身)。 */
    public?: boolean
  }
}

/** 侧边栏导航项:路径、标题与图标名。 */
export interface NavItem {
  /** 路由路径。 */
  path: string
  /** 页面标题(侧边栏显示)。 */
  title: string
  /** 路由 name。 */
  name: string
}

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '问答' },
  },
  {
    path: '/documents',
    name: 'documents',
    component: () => import('@/views/DocumentsView.vue'),
    meta: { title: '知识库文档' },
  },
  {
    path: '/search',
    name: 'search',
    component: () => import('@/views/SearchView.vue'),
    meta: { title: '检索' },
  },
  {
    path: '/stats',
    name: 'stats',
    component: () => import('@/views/StatsView.vue'),
    meta: { title: '运营统计' },
  },
  {
    path: '/query-logs',
    name: 'query-logs',
    component: () => import('@/views/QueryLogsView.vue'),
    meta: { title: '查询日志' },
  },
  {
    path: '/feedback',
    name: 'feedback',
    component: () => import('@/views/FeedbackView.vue'),
    meta: { title: '反馈' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

/** 侧边栏导航顺序:与 issue #20 的页面清单一致。 */
export const navItems: NavItem[] = [
  { path: '/', title: '问答', name: 'chat' },
  { path: '/documents', title: '知识库文档', name: 'documents' },
  { path: '/search', title: '检索', name: 'search' },
  { path: '/stats', title: '运营统计', name: 'stats' },
  { path: '/query-logs', title: '查询日志', name: 'query-logs' },
  { path: '/feedback', title: '反馈', name: 'feedback' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

/**
 * 路由守卫:除登录页外都要求持有 token。
 *
 * token 是否真正有效由服务端判定:刷新后先用 `/auth/me` 恢复主体,
 * 401 时清理并回登录页;不做本地过期时间判断(时钟不可信)。
 */
export async function requireAuth(to: {
  meta: { public?: boolean }
  fullPath: string
}): Promise<true | { name: 'login'; query: { redirect: string } }> {
  if (to.meta.public) {
    return true
  }
  const authStore = useAuthStore()
  if (!authStore.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (!authStore.principal) {
    try {
      await authStore.loadPrincipal()
    } catch {
      authStore.signOut()
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }
  return true
}

router.beforeEach(requireAuth)

export default router
