import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: true, // 监听 0.0.0.0，允许局域网其他设备访问
    proxy: {
      '/api': {
        // 注意：本机 localhost 会优先解析到 IPv6 ::1，而后端只监听 IPv4 127.0.0.1，
        // 用 localhost 会导致代理打到其他服务/连接失败，必须写 127.0.0.1；
        // 后端固定 8100 端口（8000 被本机其他服务占用）
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
      },
      '/brand': {
        // 品牌静态资源（自定义 logo）
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
      },
    },
  },
})
