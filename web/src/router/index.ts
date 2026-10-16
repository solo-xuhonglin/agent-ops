import { createRouter, createWebHistory } from 'vue-router'

import AppLayout from '../layouts/AppLayout.vue'
import { queryClient } from '../plugins/query'
import { sessionFetcher, sessionKey } from '../composables/useSession'
import HomePage from '../pages/HomePage.vue'
import LoginPage from '../pages/LoginPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', name: 'home', component: HomePage, meta: { title: '配置首页' } },
      ],
    },
    { path: '/login', name: 'login', component: LoginPage, meta: { title: '登录' } },
    { path: '/:pathMatch(.*)*', redirect: { name: 'home' } },
  ],
})

router.beforeEach(async (to) => {
  if (to.name === 'login') return true
  if (queryClient.getQueryData(sessionKey) === undefined) {
    await queryClient.fetchQuery({
      queryKey: sessionKey,
      queryFn: sessionFetcher,
      staleTime: 60_000,
      retry: false,
    })
  }
  if (!queryClient.getQueryData(sessionKey)) return { name: 'login' }
  return true
})

export default router
