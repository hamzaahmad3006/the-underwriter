import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Backend runs on :8000 locally (docker-compose or `uvicorn app.main:app`).
    // Keeps the browser same-origin in dev so CORS_ORIGINS stays prod-only (SEC-007).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // UI-011: bundle budget is 400KB gzipped. Warn well before we get there.
    chunkSizeWarningLimit: 700,
  },
})
