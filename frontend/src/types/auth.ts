/**
 * 身份与登录相关类型:契约以端 OpenAPI(`/openapi.json`)为准,手写声明。
 *
 * 术语遵循 `GLOSSARY.md`:主体 / 租户 / 角色 / 部门。
 */

/** 主体种类:人类用户或服务账号(v1 只有用户能登录)。 */
export type PrincipalKind = 'user' | 'service'

/** 角色:租户内 member / admin,跨租户 platform_operator。 */
export type PrincipalRole = 'member' | 'admin' | 'platform_operator'

/** 当前主体:租户、角色与部门都由服务端从 token 解析。 */
export interface Principal {
  id: string
  username: string | null
  kind: PrincipalKind
  tenant: string
  role: PrincipalRole
  department: string | null
  display_name: string | null
}

/** 登录结果:短期 token + 有效期 + 主体。 */
export interface LoginResult {
  access_token: string
  token_type: string
  expires_in: number
  principal: Principal
}
