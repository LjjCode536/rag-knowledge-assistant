/**
 * 接缝 B:登录页的组件行为(issue #26)。
 *
 * 断言用户可见的行为:未填完不能提交;口令错误显示稳定错误码;登录成功
 * 写入 token 并回到原目标页。不测内部状态变量与 Element Plus 自身行为。
 */
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { clearAuthToken, getAuthToken } from '@/auth/session'
import LoginView from '@/views/LoginView.vue'

/** 构造一个成功信封。 */
function okEnvelope<T>(data: T): string {
  return JSON.stringify({
    code: 'OK',
    message: 'ok',
    data,
    details: null,
    trace_id: 'trace-login-0001',
    timestamp: '2026-11-26T08:00:00Z',
  })
}

/** 构造一个失败信封。 */
function errorEnvelope(code: string, message: string): string {
  return JSON.stringify({
    code,
    message,
    data: null,
    details: null,
    trace_id: 'trace-login-err-0001',
    timestamp: '2026-11-26T08:00:00Z',
  })
}

const PRINCIPAL = {
  id: 'usr_member',
  username: 'member',
  kind: 'user',
  tenant: 'default',
  role: 'member',
  department: '研发部',
  display_name: '种子成员',
}

/** 登录页依赖路由读取 `redirect`;测试用内存路由。 */
async function mountLogin(path = '/login') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginView },
      { path: '/documents', name: 'documents', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(LoginView, {
    global: { plugins: [pinia, router, ElementPlus] },
  })
  return { wrapper, router }
}

/** 填入账号口令。 */
async function fillForm(
  wrapper: Awaited<ReturnType<typeof mountLogin>>['wrapper'],
  username = 'member',
  password = 'correct-password',
): Promise<void> {
  await wrapper.find('[data-testid="login-username"]').setValue(username)
  await wrapper.find('[data-testid="login-password"]').setValue(password)
}

describe('登录页组件行为', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    clearAuthToken()
    window.sessionStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('账号或口令为空时不能提交', async () => {
    const { wrapper } = await mountLogin()
    expect(wrapper.find('[data-testid="login-submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('租户、角色与部门由登录身份决定')
  })

  it('口令错误显示 UNAUTHENTICATED 与 trace_id,不写入 token', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(errorEnvelope('UNAUTHENTICATED', '账号或口令不正确'), { status: 401 }),
    )
    const { wrapper } = await mountLogin()
    await fillForm(wrapper, 'member', 'wrong-password')

    await wrapper.find('[data-testid="login-submit"]').trigger('click')

    await vi.waitFor(() => {
      expect(wrapper.find('[data-testid="error-code"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-testid="error-code"]').text()).toBe('UNAUTHENTICATED')
    expect(wrapper.find('[data-testid="error-trace-id"]').text()).toBe('trace-login-err-0001')
    expect(getAuthToken()).toBeNull()
  })

  it('登录成功写入 token 并回到 redirect 指向的页面', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        okEnvelope({
          access_token: 'jwt-abc',
          token_type: 'bearer',
          expires_in: 3600,
          principal: PRINCIPAL,
        }),
        { status: 200 },
      ),
    )
    const { wrapper, router } = await mountLogin('/login?redirect=/documents')
    await fillForm(wrapper)

    await wrapper.find('[data-testid="login-submit"]').trigger('click')

    await vi.waitFor(() => {
      expect(router.currentRoute.value.path).toBe('/documents')
    })
    expect(getAuthToken()).toBe('jwt-abc')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'member',
      password: 'correct-password',
    })
  })
})
