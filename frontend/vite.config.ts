import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { defineConfig } from 'vitest/config'

/**
 * Vite 配置:开发期把接口路径与健康检查路径转发到后端本地端口。
 *
 * 边界(ADR-0015):前端**不挂进 FastAPI**,后端**零改动、零 CORS**。
 * 开发期靠这里的 proxy 让前端与接口同源;生产期用同一域名反代同时服务
 * 静态产物与接口,后端仍然不需要 CORS。
 */
const BACKEND_ORIGIN = process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig(({ mode }) => ({
  plugins: [
    vue(),
    // Element Plus 按需引入:组件与 API 自动引入,不整包打进产物
    // 测试环境不注入样式:组件测试不关心 Element Plus 的样式,注入 CSS
    // 反而让 Vitest 的 SSR 转换器去处理 .css 文件
    AutoImport({
      resolvers: [ElementPlusResolver({ importStyle: mode === 'test' ? false : 'css' })],
    }),
    Components({
      resolvers: [ElementPlusResolver({ importStyle: mode === 'test' ? false : 'css' })],
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  optimizeDeps: {
    // 预打包 Element Plus 全量:按需引入的组件在**首次访问页面**时才被
    // 发现,会触发依赖优化 → 强制 reload → 打断路由的动态导入(白屏)。
    // 显式预打包让优化在 dev server 启动时一次做完,页面首访不再触发重载。
    include: ['element-plus', '@element-plus/icons-vue', 'echarts', 'vue-echarts'],
  },
  server: {
    port: 5173,
    // 同源转发:浏览器视角下前端与接口同域,后端无需任何跨源配置
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
      '/health': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 静默 Element Plus 的旧版 API 告警(本项目只用 SCSS 变量做主题定制)
        silenceDeprecations: ['legacy-js-api', 'import', 'global-builtin', 'color-functions'],
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.spec.ts'],
    restoreMocks: true,
  },
}))
