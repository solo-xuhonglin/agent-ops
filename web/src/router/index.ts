import { createRouter, createWebHistory } from 'vue-router'

import AppLayout from '../layouts/AppLayout.vue'
import HomePage from '../pages/HomePage.vue'

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
  ],
})

export default router
