/**
 * 认证状态(ADR-0020):token + 当前主体。
 *
 * token 存内存 + `sessionStorage`(演示级,生产换 OIDC + HttpOnly 会话);
 * 租户、角色与部门**只从服务端主体端点取**,前端不自造、不由界面选择。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { getCurrentPrincipal, login as loginRequest } from '@/api'
import {
  clearAuthToken,
  getAuthToken,
  setAuthToken,
} from '@/auth/session'
import type { Principal } from '@/types/auth'

export const useAuthStore = defineStore('auth', () => {
  // token 的唯一事实在 session 模块;这里是只读视图,避免两份状态分叉
  const token = computed<string | null>(() => getAuthToken())
  const principal = ref<Principal | null>(null)

  /** 是否持有 token(未过期与否由服务端 401 判定)。 */
  const isAuthenticated = computed<boolean>(() => Boolean(token.value))

  /** 登录:口令换短期 JWT,并立刻带上主体。 */
  async function signIn(username: string, password: string): Promise<Principal> {
    const result = await loginRequest(username, password)
    setAuthToken(result.access_token)
    principal.value = result.principal
    return result.principal
  }

  /**
   * 从服务端取当前主体(刷新页面后恢复会话)。
   *
   * 失败(401 / 网络)原样抛出:由调用方(路由守卫)决定回登录页。
   */
  async function loadPrincipal(): Promise<Principal> {
    const current = await getCurrentPrincipal()
    principal.value = current
    return current
  }

  /** 登出:清 token 与主体;不调用服务端(无服务端会话)。 */
  function signOut(): void {
    clearAuthToken()
    principal.value = null
  }

  return { token, principal, isAuthenticated, signIn, loadPrincipal, signOut }
})
