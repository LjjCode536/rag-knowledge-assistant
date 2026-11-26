/**
 * 演示级会话存储(ADR-0020):token 存内存 + `sessionStorage`。
 *
 * 这是为了本地可演示的取舍,不是生产级会话:XSS 可读取 sessionStorage。
 * 生产应换 OIDC 授权码 + PKCE 与 HttpOnly 会话。
 *
 * 本模块不依赖 Pinia / 路由:API 客户端层注入 token 与上报 401 只认这里,
 * 避免 `client.ts → store → client.ts` 的循环依赖。
 */

import { ref } from 'vue'

const TOKEN_STORAGE_KEY = 'rag.auth.token'

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    // 隐私模式 / 禁用存储时退化为仅内存
    return null
  }
}

// 唯一事实:响应式 ref 让 Pinia store 与 API 层看到同一份 token,不各自镜像。
const token = ref<string | null>(storage()?.getItem(TOKEN_STORAGE_KEY) ?? null)

type UnauthorizedListener = () => void

const unauthorizedListeners = new Set<UnauthorizedListener>()

/** 当前 token;未登录为 `null`。 */
export function getAuthToken(): string | null {
  return token.value
}

/** 登录成功后写入 token(内存 + sessionStorage)。 */
export function setAuthToken(value: string): void {
  token.value = value
  storage()?.setItem(TOKEN_STORAGE_KEY, value)
}

/** 登出 / 凭证失效:清理两处存储。 */
export function clearAuthToken(): void {
  token.value = null
  storage()?.removeItem(TOKEN_STORAGE_KEY)
}

/**
 * 订阅"请求遇到 401"。
 *
 * API 层在**原本带着 token** 的请求遇到 401 时清理 token 并通知;
 * 回登录页由应用外壳处理,客户端层不依赖路由。
 */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => {
    unauthorizedListeners.delete(listener)
  }
}

/** 清理凭证并通知订阅者;重复 401 不会重复通知(token 已清空)。 */
export function notifyUnauthorized(): void {
  clearAuthToken()
  for (const listener of unauthorizedListeners) {
    listener()
  }
}
