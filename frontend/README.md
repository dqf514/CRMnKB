# 榜样CRM 前端

Vue 3 + Vite + Element Plus 实现的前端，对接 FastAPI 后端（http://127.0.0.1:8100）。

## 技术栈

- Vue 3（`<script setup>`）+ Vue Router + Pinia
- Element Plus + @element-plus/icons-vue（明暗双主题：`theme-chalk/dark/css-vars.css` + 动态 `--el-color-primary` 色阶）
- Axios（请求/响应拦截器封装于 `src/api/index.js`）
- markdown-it + highlight.js（AI 回答 / 报告的 Markdown 渲染与代码高亮）
- ECharts（管理端 LLM 用量统计等图表，`src/utils/useChart.js` 封装）
- mammoth（docx 在线预览转 HTML；xlsx 仅用于后端 openpyxl 的 Excel 导入导出，不在线预览）
- 流式问答：fetch + ReadableStream 解析 SSE（`src/api/chatStream.js`）

## 快速开始

```bash
npm install
npm run dev      # 开发环境，默认 http://localhost:5173，/api 代理到 http://127.0.0.1:8100
npm run build    # 生产构建，输出 dist/
npm run preview  # 预览构建产物
```

默认登录账号：admin / admin123

## 目录结构

```
src/
├── api/index.js        # axios 封装 + 全部接口函数
├── api/chatStream.js   # SSE 流式问答（fetch 实现，支持 kb_ids 范围）
├── api/libraryUpload.js # 文档库上传封装（多文件/文件夹，复用于客户文档）
├── components/FilePreview.vue  # 文件在线预览弹层（Office/PDF/图片/音视频）
├── layouts/AppLayout.vue  # 响应式布局（<992px 抽屉菜单）+ 通知铃铛 + 主题控件
├── router/index.js     # 路由 + 登录守卫（未登录跳登录页，admin 路由限管理员）
├── stores/auth.js      # 认证状态（Pinia）
├── stores/theme.js     # 主题状态：明暗模式 / 品牌色 / 每页条数（localStorage + 后端偏好双写）
├── stores/industries.js # 行业标签字典（七期）
├── styles/index.css    # 全局设计令牌（圆角/阴影/间距）与组件覆盖
├── styles/markdown.css # Markdown 渲染样式（AI 回答/报告）
├── utils/format.js     # 枚举中文映射 / el-tag 颜色 / 时间与文件大小格式化
│                       # （parseServerDate：后端 UTC 时间统一转本地时区）
├── utils/filePreview.js # 预览类型判定与 URL 组装（配合 FilePreview.vue）
├── utils/useChart.js   # ECharts 封装（按需初始化、主题联动、resize）
├── utils/markdown.js   # markdown-it 实例 + highlight.js 注册
└── views/              # Login / Customers / CustomerDetail / Knowledge / KbDetail / Library /
                        # Chat / Tasks / Workflows / Reports / Profile
                        # admin/ 下为系统管理页（仅 admin）：
                        #   Users / Groups / LlmModels / LlmStats / System / Errors /
                        #   RecycleBin（回收站）/ AuditLogs（审计日志）
```
