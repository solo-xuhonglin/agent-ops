<script setup lang="ts">
import { computed } from 'vue'

import { useSessionQuery } from '../composables/useSession'

const { data: session } = useSessionQuery()

const ROLE_LABELS: Record<string, string> = { admin: '管理员', editor: '编辑', viewer: '只读' }
const ROLE_CHIP: Record<string, string> = { editor: 'chip--editor', viewer: 'chip--readonly' }
const currentTeam = computed(() =>
  session.value?.teams.find((team) => team.tenant_id === session.value?.current_tenant_id),
)
</script>

<template>
  <div class="pagehead">
    <h1 class="page-title">配置首页</h1>
    <span class="pagehead-sub">欢迎，{{ session?.account.email }}</span>
  </div>

  <p class="lede">领域导航将随各领域功能上线逐个点亮。运行只认已发布的版本；每一次变更都有审计。</p>

  <div class="team-line">
    <template v-if="currentTeam">
      <span class="micro-label">当前团队</span>
      <span class="team-name">{{ currentTeam.tenant_name }}</span>
      <span :class="['chip', ROLE_CHIP[currentTeam.role]]">
        {{ ROLE_LABELS[currentTeam.role] ?? currentTeam.role }}
      </span>
    </template>
    <template v-else>
      <span class="micro-label">当前团队</span>
      <span class="team-name">暂无团队</span>
    </template>
  </div>
</template>

<style scoped>
.team-line {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding-top: var(--sp-4);
  border-top: 1px solid var(--hairline);
}

.team-name {
  font-size: 14px;
}
</style>
