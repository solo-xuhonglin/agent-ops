<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'

const drawer = ref(true)
const route = useRoute()
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
    <v-btn icon="mdi-account-circle" />
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
