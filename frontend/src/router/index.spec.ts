/**
 * 接缝:路由守卫(issue #26)。
 *
 * 未登录访问受保护页面时回登录页,并带上原目标路径;登录页本身是公开的。
 */
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'

import { clearAuthToken } from '@/auth/session'
import { requireAuth } from '@/router'

describe('路由守卫', () => {
  it('未登录访问受保护页面时回登录页并记住目标路径', async () => {
    clearAuthToken()
    setActivePinia(createPinia())

    const result = await requireAuth({ meta: {}, fullPath: '/documents?keyword=手册' })

    expect(result).toEqual({
      name: 'login',
      query: { redirect: '/documents?keyword=手册' },
    })
  })

  it('登录页保持公开', async () => {
    clearAuthToken()
    setActivePinia(createPinia())

    await expect(requireAuth({ meta: { public: true }, fullPath: '/login' })).resolves.toBe(true)
  })
})
