import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  server: {
    port: 5173,
    proxy: {
      // Dev rides the real edge so cookies and error envelopes behave as in prod.
      '/api': 'http://localhost:8080',
    },
  },
})
