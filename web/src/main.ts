import '@mdi/font/css/materialdesignicons.css'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createApp } from 'vue'

import App from './App.vue'
import { queryClient } from './plugins/query'
import vuetify from './plugins/vuetify'
import router from './router'
import './style.css'

createApp(App)
  .use(vuetify)
  .use(VueQueryPlugin, { queryClient })
  .use(router)
  .mount('#app')
