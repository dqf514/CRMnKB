<template>
  <div class="nf-page">
    <div class="nf-code">404</div>
    <div class="nf-text">页面不存在或已被移动</div>
    <el-button type="primary" @click="router.push('/today')">返回首页</el-button>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.nf-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  min-height: 60vh;
}
.nf-code {
  font-size: 64px;
  font-weight: 700;
  color: var(--app-ink-2);
}
.nf-text {
  font-size: 15px;
  color: var(--app-ink-2);
}
</style>
