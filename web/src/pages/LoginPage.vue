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
  <div class="split">
    <aside class="brand-side">
      <div class="mark display-serif">Agent<br />Ops.</div>
      <div class="zh">配置中心</div>
      <p class="desc">定义、发布与权限的唯一入口。运行只认已发布的版本；每一次变更都有审计。</p>
    </aside>

    <main class="form-side">
      <form class="card form-stack" @submit.prevent="submit">
        <div class="micro-label num">NO. 01 — ACCESS</div>
        <h2 class="page-title">登录</h2>

        <v-text-field
          v-model="email"
          label="邮箱"
          type="email"
          autocomplete="username"
          variant="underlined"
        />
        <v-text-field
          v-model="password"
          label="口令"
          type="password"
          autocomplete="current-password"
          variant="underlined"
        />

        <v-alert v-if="errorMessage" type="error" variant="tonal" density="compact" class="error">
          {{ errorMessage }}
        </v-alert>

        <button type="submit" class="go" :disabled="login.isPending.value">进 入</button>
      </form>
    </main>
  </div>
</template>

<style scoped>
.split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  min-height: 100svh;
  background: var(--canvas);
}

.brand-side {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 0 10%;
}

.mark {
  font-size: 56px;
  line-height: 1.15;
  color: var(--accent);
}

.zh {
  margin-top: var(--sp-3);
  font-size: 13px;
  letter-spacing: 0.18em;
  color: var(--ink-2);
}

.desc {
  margin-top: var(--sp-6);
  font-size: 12.5px;
  line-height: 2;
  color: var(--ink-2);
  max-width: 30em;
  border-top: 1px solid var(--hairline);
  padding-top: var(--sp-5);
}

.form-side {
  display: grid;
  place-items: center;
  padding: var(--sp-8);
}

.card {
  width: min(380px, 100%);
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 4px;
  padding: var(--sp-8);
}

.error {
  margin-bottom: var(--sp-4);
}

.go {
  all: unset;
  display: grid;
  place-items: center;
  width: 100%;
  height: 44px;
  background: var(--accent);
  color: var(--on-accent);
  font-size: 13px;
  letter-spacing: 0.3em;
  text-indent: 0.3em;
  border-radius: 4px;
  margin-top: var(--sp-5);
  cursor: pointer;
  transition: background 0.18s ease;
}

.go:hover {
  background: var(--accent-deep);
}

.go:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 2px;
}

.go:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
