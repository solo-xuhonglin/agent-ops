<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import { useLogout, useSessionQuery, useSwitchTeam } from '../composables/useSession'

const drawer = ref(true)
const route = useRoute()
const { data: session } = useSessionQuery()
const switchTeam = useSwitchTeam()
const logout = useLogout()

const ROLE_LABELS: Record<string, string> = { admin: '管理员', editor: '编辑', viewer: '只读' }
</script>

<template>
  <v-navigation-drawer v-model="drawer">
    <div class="brand ma-4">
      <div class="brand-name">AgentOps</div>
      <div class="brand-sub text-medium-emphasis">配置中心</div>
    </div>

    <v-list nav density="compact">
      <v-list-subheader>配置</v-list-subheader>
      <v-list-item
        prepend-icon="mdi-home-outline"
        title="配置首页"
        :to="{ name: 'home' }"
        rounded="lg"
      />
    </v-list>

    <template #append>
      <v-divider />
      <v-list-item
        :prepend-icon="drawer ? 'mdi-chevron-left' : 'mdi-chevron-right'"
        :title="drawer ? '收起侧栏' : undefined"
        @click="drawer = !drawer"
      />
    </template>
  </v-navigation-drawer>

  <v-app-bar flat border>
    <v-app-bar-nav-icon @click="drawer = !drawer" />
    <v-breadcrumbs :items="[String(route.meta.title ?? '')]" />
    <v-spacer />
    <v-menu>
      <template #activator="{ props }">
        <v-btn v-bind="props" icon="mdi-account-circle" />
      </template>
      <v-list density="compact" min-width="240">
        <v-list-item :title="session?.account.email ?? ''" subtitle="当前账号" />
        <v-divider />
        <template v-if="session?.teams.length">
          <v-list-subheader>切换团队</v-list-subheader>
          <v-list-item
            v-for="team in session?.teams"
            :key="team.tenant_id"
            :title="team.tenant_name"
            :subtitle="ROLE_LABELS[team.role] ?? team.role"
            :active="team.tenant_id === session?.current_tenant_id"
            @click="switchTeam.mutate(team.tenant_id)"
          />
        </template>
        <v-list-item v-else title="暂无团队" disabled />
        <v-divider />
        <v-list-item prepend-icon="mdi-logout" title="登出" @click="logout.mutate()" />
      </v-list>
    </v-menu>
  </v-app-bar>

  <v-main>
    <v-container fluid>
      <router-view />
    </v-container>
  </v-main>
</template>

<style scoped>
.brand-name {
  font-size: 1.25rem;
  font-weight: 600;
  color: rgb(var(--v-theme-primary));
}

.brand-sub {
  font-size: 0.8rem;
}
</style>
