// Vitest 配置：只测纯逻辑（utils / stores / api 的 SSE 解析），
// 不挂载组件，所以不需要 @vitejs/plugin-vue 与 jsdom，node 环境即可。
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    setupFiles: ['tests/setup.js'],
    include: ['tests/**/*.test.js'],
  },
})
