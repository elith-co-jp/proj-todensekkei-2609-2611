import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
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
    test: {
      coverage: {
        provider: 'v8',
        // テストが読み込まないファイルも数える（読み込まれないファイルが網羅率から漏れないように）
        include: ['src/**/*.{ts,tsx}'],
        exclude: [
          'src/**/*.test.{ts,tsx}',
          'src/test/**',
          'src/vite-env.d.ts',
          // 型の宣言だけ（実行される文が無い）
          'src/types.ts',
          // ReactDOM.createRoot で #root に描くだけの入口。ルートの定義は routes.tsx でテストする
          'src/main.tsx',
        ],
        reporter: ['text', 'text-summary', 'html'],
        thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
      },
    },
  }
})
