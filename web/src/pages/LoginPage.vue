<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiClientError } from '../api/client'
import { useLogin } from '../composables/useSession'

const router = useRouter()
const login = useLogin()

const email = ref('')
const password = ref('')
const errorMessage = ref<string | null>(null)

async function submit() {
  errorMessage.value = null
  try {
    await login.mutateAsync({ email: email.value, password: password.value })
    await router.push({ name: 'home' })
  } catch (error) {
    // Fixed copy for credential failures so the UI never distinguishes them.
    errorMessage.value =
      error instanceof ApiClientError && error.status === 401
        ? '邮箱或口令不正确'
        : error instanceof ApiClientError
          ? error.message
          : '登录失败，请稍后重试'
  }
}
</script>

<template>
  <v-main class="login-main">
    <v-card class="login-card" elevation="4" prepend-icon="mdi-shield-key-outline" title="登录 AgentOps">
      <v-card-text>
        <v-form @submit.prevent="submit">
          <v-text-field
            v-model="email"
            label="邮箱"
            type="email"
            autocomplete="username"
            variant="outlined"
            density="comfortable"
          />
          <v-text-field
            v-model="password"
            label="口令"
            type="password"
            autocomplete="current-password"
            variant="outlined"
            density="comfortable"
          />
          <v-alert v-if="errorMessage" type="error" variant="tonal" density="compact" class="mb-4">
            {{ errorMessage }}
          </v-alert>
          <v-btn
            type="submit"
            block
            color="primary"
            size="large"
            :loading="login.isPending.value"
          >
            登录
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-main>
</template>

<style scoped>
.login-main {
  display: grid;
  place-items: center;
  min-height: 100svh;
  background: linear-gradient(135deg, #8b2fc9 0%, #c03fd8 100%);
}

.login-card {
  width: min(400px, calc(100vw - 32px));
}
</style>
