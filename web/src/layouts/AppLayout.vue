<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useLogout, useSessionQuery, useSwitchTeam } from '../composables/useSession'

const drawer = ref(true)
const route = useRoute()
const router = useRouter()
const { data: session } = useSessionQuery()
const switchTeam = useSwitchTeam()
const logout = useLogout()

// A session that dies mid-page (expired, revoked) sends the user back to
// login; the router guard only covers cold loads.
watch(session, (value, previous) => {
  if (previous && !value) router.push({ name: 'login' })
})

const ROLE_LABELS: Record<string, string> = { admin: '管理员', editor: '编辑', viewer: '只读' }
const initial = computed(() => session.value?.account.email.charAt(0).toUpperCase() ?? '')
const currentTeam = computed(() =>
  session.value?.teams.find((team) => team.tenant_id === session.value?.current_tenant_id),
)
</script>

<template>
  <v-navigation-drawer v-model="drawer" width="232" class="shell-drawer">
    <div class="brand">
      <div class="brand-word display-serif">AgentOps</div>
      <div class="brand-sub micro-label">配置中心</div>
    </div>

    <v-list class="nav">
      <div class="micro-label nav-group">配置</div>
      <v-list-item
        prepend-icon="mdi-home-outline"
        title="配置首页"
        :to="{ name: 'home' }"
      />
    </v-list>
  </v-navigation-drawer>

  <v-app-bar class="topbar">
    <v-app-bar-nav-icon @click="drawer = !drawer" />
    <div class="crumb">配置 / <b>{{ route.meta.title }}</b></div>
    <v-spacer />
    <v-menu location="bottom start" origin="top start">
      <template #activator="{ props }">
        <button v-bind="props" class="userchip">
          <span class="av">{{ initial }}</span>
          <span v-if="currentTeam" class="team">{{ currentTeam.tenant_name }} ·</span>
          <span class="email">{{ session?.account.email }}</span>
          <v-icon size="18" icon="mdi-chevron-down" />
        </button>
      </template>
      <v-list min-width="248" class="user-menu">
        <div class="menu-head">
          <div class="micro-label">当前账号</div>
          <div class="menu-email">{{ session?.account.email }}</div>
        </div>
        <v-divider />
        <template v-if="session?.teams.length">
          <div class="micro-label menu-group">切换团队</div>
          <v-list-item
            v-for="team in session.teams"
            :key="team.tenant_id"
            :title="team.tenant_name"
            :subtitle="ROLE_LABELS[team.role] ?? team.role"
            :active="team.tenant_id === session.current_tenant_id"
            @click="switchTeam.mutate(team.tenant_id)"
          />
        </template>
        <v-list-item v-else title="暂无团队" disabled />
        <v-divider />
        <v-list-item prepend-icon="mdi-logout" title="登出" @click="logout.mutate()" />
      </v-list>
    </v-menu>
  </v-app-bar>

  <v-main class="workspace">
    <div class="page-pad">
      <router-view />
    </div>
  </v-main>
</template>

<style scoped>
.shell-drawer {
  border-right: 1px solid var(--hairline);
}

.brand {
  padding: var(--sp-6) var(--sp-5) var(--sp-6);
}

.brand-word {
  color: var(--accent);
}

.brand-sub {
  margin-top: var(--sp-1);
}

.nav-group {
  padding: 0 var(--sp-5);
  margin-bottom: var(--sp-1);
}

.nav :deep(.v-list-item) {
  padding-inline: var(--sp-5);
  min-height: 40px;
  border-radius: 0;
  color: var(--ink-2);
}

.nav :deep(.v-list-item:hover) {
  color: var(--ink);
}

.nav :deep(.v-list-item--active) {
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 3px 0 0 var(--accent);
}

.topbar {
  border-bottom: 1px solid var(--hairline);
}

.crumb {
  font-size: 12.5px;
  color: var(--ink-2);
}

.crumb b {
  color: var(--ink);
  font-weight: 500;
}

.userchip {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  border: none;
  background: transparent;
  font-size: 12.5px;
  color: var(--ink-2);
  cursor: pointer;
  padding: var(--sp-1);
}

.userchip .team {
  color: var(--ink);
}

.userchip .email {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.userchip .av {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--accent);
  color: var(--on-accent);
  display: grid;
  place-items: center;
  font-size: 11px;
}

.workspace {
  background: var(--surface);
}

.page-pad {
  padding: var(--sp-6) var(--sp-8);
  max-width: 1200px;
}

.menu-head {
  padding: var(--sp-3) var(--sp-4) var(--sp-2);
}

.menu-email {
  font-size: 13.5px;
  color: var(--ink);
  margin-top: 2px;
}

.menu-group {
  padding: var(--sp-2) var(--sp-4) var(--sp-1);
}
</style>
