import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// https://vite.dev/config/
//
// Krystal Radio: `agentBridge` — адрес локального HTTP-моста между
// LLM-агентами (backend/) и этим интерфейсом. Проксируется в dev,
// чтобы не было CORS и cross-origin проблем с GLB/PNG/SVG из out/.
const BRIDGE = process.env.KR_BRIDGE ?? 'http://127.0.0.1:8787';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  optimizeDeps: {
    exclude: ['lucide-react', 'tesseract.js'],
  },
  server: {
    port: 5173,
    proxy: {
      // Агентный мост: сборка, артефакты, метрики, агенты.
      // WebSocket-путь оставлен на случай, если bridge будет расширен.
      '/api': {
        target: BRIDGE,
        changeOrigin: true,
        secure: false,
      },
      // Прямой доступ к артефактам out/ (3D-модель, чертежи, водопад).
      '/out': {
        target: BRIDGE,
        changeOrigin: true,
        secure: false,
      },
    },
  },
});