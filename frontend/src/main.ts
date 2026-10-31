/**
 * 应用入口:装配路由、Pinia 与 TanStack Query,并引入主题与基础样式。
 */
import { VueQueryPlugin } from '@tanstack/vue-query'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/theme.scss'
import './styles/main.scss'

const app = createApp(App)

// Element Plus 官方中文语言包:分页 / 表格 / 选择器等组件的内置文案走中文,
// 不在每个组件里各写一份(避免同义漂移)。
app.use(ElementPlus, { locale: zhCn })

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

// 跨页面客户端状态(当前租户筛选)归 Pinia
app.use(createPinia())
app.use(router)
app.mount('#app')
