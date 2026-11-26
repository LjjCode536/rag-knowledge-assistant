/**
 * 应用入口:装配路由、Pinia 与 TanStack Query,并引入主题与基础样式。
 */
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { onUnauthorized } from './auth/session'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles/theme.scss'
import './styles/main.scss'

const app = createApp(App)

// 服务端状态归 TanStack Query:查询、统计、文档档案、轮询都在这里
app.use(VueQueryPlugin, {
  queryClientConfig: {
    defaultOptions: {
      queries: {
        // 入库进度等状态不入缓存,避免界面显示过期的进度
        staleTime: 0,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  },
})

// 跨页面客户端状态(登录 token 与当前主体)归 Pinia
const pinia = createPinia()
app.use(pinia)

// 401:清凭证并回登录页;客户端层只上报,跳转由外壳决定(ADR-0020)
const authStore = useAuthStore(pinia)
onUnauthorized(() => {
  authStore.signOut()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({
      name: 'login',
      query: { redirect: current.fullPath },
    })
  }
})

app.use(router)
app.mount('#app')
