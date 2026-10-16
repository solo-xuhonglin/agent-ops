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
    <aside class="panel">
      <div class="mark display-serif">Agent<br />Ops.</div>
      <div class="zh">配置中心</div>
      <p class="desc">定义、发布与权限的唯一入口。运行只认已发布的版本；每一次变更都有审计。</p>
    </aside>

    <main class="form-side">
      <form class="inner" @submit.prevent="submit">
        <div class="micro-label num-label">NO. 01 — ACCESS</div>
        <h2 class="display-serif heading">登录</h2>

        <v-text-field
          v-model="email"
          label="邮箱"
          type="email"
          autocomplete="username"
          variant="underlined"
          density="comfortable"
        />
        <v-text-field
          v-model="password"
          label="口令"
          type="password"
          autocomplete="current-password"
          variant="underlined"
          density="comfortable"
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
  grid-template-columns: 46% 1fr;
  min-height: 100svh;
}

.panel {
  background: var(--brand-panel);
  color: #f4effa;
  padding: 7svh 6% 6svh;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.panel::after {
  content: '';
  position: absolute;
  inset: auto -30% -45% auto;
  width: 70%;
  aspect-ratio: 1;
  border: 1px solid rgba(244, 239, 250, 0.18);
  border-radius: 50%;
}

.mark {
  font-size: 46px;
  line-height: 1.15;
}

.zh {
  margin-top: var(--sp-3);
  font-size: 14px;
  letter-spacing: 0.55em;
  opacity: 0.85;
}

.desc {
  margin-top: auto;
  font-size: 12.5px;
  line-height: 2;
  opacity: 0.72;
  max-width: 34em;
  border-top: 1px solid rgba(244, 239, 250, 0.25);
  padding-top: var(--sp-5);
}

.form-side {
  background: var(--canvas);
  display: grid;
  place-items: center;
  padding: 0 8%;
}

.inner {
  width: min(380px, 100%);
}

.num-label {
  font-family: var(--font-mono);
  margin-bottom: var(--sp-1);
}

.heading {
  font-size: 18px;
  margin-bottom: var(--sp-5);
}

.inner :deep(.v-text-field) {
  margin-bottom: var(--sp-4);
}

.error {
  margin-bottom: var(--sp-4);
}

.go {
  all: unset;
  display: grid;
  place-items: center;
  height: 44px;
  background: var(--ink);
  color: #fff;
  font-size: 13px;
  letter-spacing: 0.3em;
  text-indent: 0.3em;
  margin-top: var(--sp-5);
  cursor: pointer;
  transition: background 0.18s ease;
}

.go:hover {
  background: var(--accent);
}

.go:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
