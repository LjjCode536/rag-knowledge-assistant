<script setup lang="ts">
/**
 * 登录页:用种子账号换取短期 JWT(ADR-0020)。
 *
 * 登录成功后回到原目标页(`redirect` 查询参数);口令错误由后端返回
 * `401 UNAUTHENTICATED`,界面展示错误码与 trace_id,不区分"账号不存在 /
 * 口令错误"(服务端也不区分)。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ErrorDetails from '@/components/ErrorDetails.vue'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const error = ref<unknown>(null)
/** 登录请求在途(避免重复提交)。 */
const submitting = ref(false)

const canSubmit = computed(
  () => username.value.trim() !== '' && password.value !== '' && !submitting.value,
)

async function onSubmit(): Promise<void> {
  if (!canSubmit.value) {
    return
  }
  error.value = null
  submitting.value = true
  try {
    await authStore.signIn(username.value.trim(), password.value)
    const redirect = route.query.redirect
    await router.replace(typeof redirect === 'string' && redirect ? redirect : '/')
  } catch (cause) {
    error.value = cause
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card
      class="login-card"
      shadow="never"
    >
      <template #header>
        <div class="login-card__title">
          企业 AI 知识库
        </div>
      </template>

      <p class="page-hint login-card__hint">
        用组织账号登录。租户、角色与部门由登录身份决定,不再由界面选择。
      </p>

      <el-form
        label-width="70px"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="账号">
          <el-input
            v-model="username"
            placeholder="登录名"
            autocomplete="username"
            data-testid="login-username"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-form-item label="口令">
          <el-input
            v-model="password"
            type="password"
            show-password
            placeholder="口令"
            autocomplete="current-password"
            data-testid="login-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!canSubmit"
            data-testid="login-submit"
            @click="onSubmit"
          >
            登录
          </el-button>
        </el-form-item>
      </el-form>

      <ErrorDetails
        v-if="error"
        :error="error"
      />
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.login-card {
  width: 100%;
  max-width: 420px;
}

.login-card__title {
  font-size: 16px;
  font-weight: 600;
}

.login-card__hint {
  margin-top: 0;
}
</style>
