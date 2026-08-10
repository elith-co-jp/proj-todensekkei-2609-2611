import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:8010'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: { '/api': { target: apiProxyTarget, changeOrigin: true } },
    },
    build: { outDir: 'dist', sourcemap: false },
  }
})
