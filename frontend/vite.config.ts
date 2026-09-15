import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// Dev server proxies /api to the FastAPI backend.
// Port injected by Electron at launch; defaults to the fixed backend port 8765.
const backendPort = process.env.CAREERNOVA_BACKEND_PORT || '8765'

export default defineConfig({
  // file:// 加载（Electron 打包态 loadFile dist/index.html）需相对路径引 JS/CSS；
  // dev 下 './' 等价 '/'，无影响。
  base: './',
  plugins: [react()],
  build: {
    // 产物统一落到项目根的 dist/ 下（唯一产物根：backend/ + frontend/ + installer/，
    // CI 只需清空/上传这一个目录）。
    // outDir 落在 vite 根（frontend/）之外，Vite 默认会「拒绝清空 + 警告」；
    // 这里显式 emptyOutDir:false 确认不清空——dist/ 下还有 backend/、installer/ 等
    // 兄弟产物，绝不能由前端构建来清空。整目录清理由 CI 的 `rm -rf dist` 负责。
    outDir: '../dist/frontend',
    emptyOutDir: false,
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
})
