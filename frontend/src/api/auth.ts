/**
 * 身份端点的调用函数。
 *
 * 登录是公开端点;当前主体需要携带 token(由 `client.ts` 统一注入)。
 */
import type { LoginResult, Principal } from '@/types/auth'

import { request } from './client'

const AUTH_PATH = '/api/v1/auth'

/** 登录:正确口令换取短期 JWT;错误口令由 `client` 层抛 `UNAUTHENTICATED`。 */
export async function login(username: string, password: string) {
  const result = await request<LoginResult>(`${AUTH_PATH}/login`, {
    method: 'POST',
    body: { username, password },
  })
  return result.data
}

/** 当前主体:租户、角色与部门全部来自服务端解析的 token。 */
export async function getCurrentPrincipal() {
  const result = await request<Principal>(`${AUTH_PATH}/me`)
  return result.data
}
