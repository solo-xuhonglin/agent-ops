<script setup lang="ts">
import { useSessionQuery } from '../composables/useSession'

const { data: session } = useSessionQuery()

const currentTeam = () => session.value?.teams.find((team) => team.tenant_id === session.value?.current_tenant_id)
</script>

<template>
  <v-card max-width="480" prepend-icon="mdi-view-dashboard-outline" title="配置首页">
    <v-card-text>
      <p class="text-medium-emphasis mb-2">欢迎使用 AgentOps 配置中心。领域导航将随各领域功能上线逐个点亮。</p>
      <p v-if="currentTeam()">
        当前团队：<strong>{{ currentTeam()!.tenant_name }}</strong>
        <v-chip size="small" class="ml-2" color="primary" variant="tonal">
          {{ currentTeam()!.role === 'admin' ? '管理员' : currentTeam()!.role === 'editor' ? '编辑' : '只读' }}
        </v-chip>
      </p>
      <p v-else class="text-medium-emphasis">暂无团队</p>
    </v-card-text>
  </v-card>
</template>
