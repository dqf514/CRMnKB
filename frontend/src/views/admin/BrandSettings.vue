<template>
  <el-card>
    <template #header>品牌设置</template>
    <el-form label-width="110px" style="max-width: 520px">
      <el-form-item label="系统名称">
        <el-input v-model="systemName" maxlength="100" placeholder="显示在侧边栏 / 登录页 / 浏览器标题" />
      </el-form-item>
      <el-form-item label="Logo 预览">
        <div class="logo-preview">
          <img :src="logoUrl" alt="logo" class="logo-img" />
          <span class="logo-name">{{ systemName || '系统名称' }}</span>
        </div>
      </el-form-item>
      <el-form-item label="自定义 Logo">
        <el-upload :show-file-list="false" :auto-upload="false" accept=".png,.jpg,.jpeg,.webp,.svg" :on-change="onLogoChange">
          <el-button :icon="Upload">选择图片</el-button>
        </el-upload>
        <span class="hint">支持 png/jpg/jpeg/webp/svg，≤5MB；选择后即时预览，点「保存」生效</span>
      </el-form-item>
    </el-form>
    <div class="footer">
      <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getBrand, updateBrand, uploadBrandLogo } from '../../api'
import { useBrandStore } from '../../stores/brand'

const brandStore = useBrandStore()
const systemName = ref('')
const logoUrl = ref('/logo-256.png')
const pendingLogo = ref(null)
const saving = ref(false)

async function load() {
  try {
    const res = await getBrand()
    systemName.value = res.system_name || '榜样知识库'
    logoUrl.value = res.logo_url || '/logo-256.png'
  } catch { /* 默认品牌 */ }
}

function onLogoChange(uploadFile) {
  pendingLogo.value = uploadFile.raw
  if (pendingLogo.value) {
    logoUrl.value = URL.createObjectURL(pendingLogo.value)
  }
}

async function handleSave() {
  saving.value = true
  try {
    if (systemName.value.trim()) {
      await updateBrand({ system_name: systemName.value.trim() })
    }
    let res = null
    if (pendingLogo.value) {
      res = await uploadBrandLogo(pendingLogo.value)
    }
    if (res) {
      logoUrl.value = res.logo_url || logoUrl.value
      systemName.value = res.system_name || systemName.value
      pendingLogo.value = null
    }
    brandStore.apply({ system_name: systemName.value, logo_url: logoUrl.value })
    ElMessage.success('已保存')
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.logo-preview {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--app-line);
  border-radius: 10px;
  background: var(--app-bg);
}
.logo-img {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  object-fit: contain;
  background: #fff;
}
.logo-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--app-ink);
}
.hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.footer {
  margin-top: 8px;
  padding-left: 110px;
}
</style>
