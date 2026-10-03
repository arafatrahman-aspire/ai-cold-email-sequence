import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The browser calls /api/*; Vite forwards it to the FastAPI service, so the
// backend needs no CORS setup. Point BACKEND_URL elsewhere if it is not local.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.BACKEND_URL || 'http://localhost:8080'
  const proxy = {
    '/api': {
      target,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
  }
  return {
    plugins: [react()],
    server: { port: 5173, proxy },
    preview: { port: 4173, proxy },
  }
})
