<template>
  <el-card>
    <el-tabs v-model="activeTab">
      <!-- 品牌设置 -->
      <el-tab-pane label="品牌设置" name="brand">
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
      </el-tab-pane>

      <!-- 解析文件格式 -->
      <el-tab-pane label="解析文件格式" name="formats">
        <div class="hint formats-hint">
          关闭的格式在文件上传/关联时<strong>不做解析</strong>（仅入库存储），避免环境不支持时白跑解析；
          启用后，存量未解析文件会在服务重启时自动补解析。图片/音频/视频解析依赖已配置的视觉/转写模型。
        </div>
        <div v-for="g in groups" :key="g.category" class="fmt-group">
          <div class="fmt-group-head">
            <span class="fmt-group-label">{{ g.label }}</span>
            <el-button link type="primary" @click="setGroup(g, true)">全启用</el-button>
            <el-button link type="danger" @click="setGroup(g, false)">全部关闭</el-button>
          </div>
          <div class="fmt-chips">
            <div v-for="it in g.items" :key="it.ext" class="fmt-chip">
              <el-switch v-model="enabledMap[it.ext]" size="small" />
              <span class="ext">{{ it.ext }}</span>
              <el-tag v-if="!enabledMap[it.ext]" size="small" type="info" class="off-tag">不解析</el-tag>
            </div>
          </div>
        </div>
        <div class="footer">
          <el-button type="primary" :loading="saving" @click="saveFormats">保存</el-button>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getBrand, updateBrand, uploadBrandLogo, getParseFormats, updateParseFormats } from '../../api'
import { useBrandStore } from '../../stores/brand'

const brandStore = useBrandStore()

// ========== 品牌设置 ==========
const systemName = ref('')
const logoUrl = ref('/logo-256.png')
const pendingLogo = ref(null)

async function loadBrand() {
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

// ========== 解析文件格式 ==========
const formatItems = ref([])
const enabledMap = reactive({})

async function loadFormats() {
  try {
    const res = await getParseFormats()
    formatItems.value = (res && res.items) || []
    for (const it of formatItems.value) enabledMap[it.ext] = it.enabled
  } catch {
    formatItems.value = []
  }
}

const groups = computed(() => {
  const m = {}
  for (const it of formatItems.value) {
    if (!m[it.category]) m[it.category] = { category: it.category, label: it.category_label, items: [] }
    m[it.category].items.push(it)
  }
  return Object.values(m)
})

function setGroup(g, val) {
  for (const it of g.items) enabledMap[it.ext] = val
}

async function saveFormats() {
  saving.value = true
  try {
    const enabled = formatItems.value.filter((it) => enabledMap[it.ext]).map((it) => it.ext)
    await updateParseFormats({ enabled })
    ElMessage.success('已保存')
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// ========== 公共 ==========
const activeTab = ref('brand')
const saving = ref(false)

onMounted(() => {
  loadBrand()
  loadFormats()
})
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
.formats-hint {
  margin: 0 0 16px;
  line-height: 1.7;
}
.fmt-group {
  margin-bottom: 18px;
}
.fmt-group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.fmt-group-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-ink);
}
.fmt-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.fmt-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--app-line);
  border-radius: 8px;
  background: var(--app-bg);
}
.ext {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-ink);
}
.off-tag {
  transform: scale(0.9);
}
.footer {
  margin-top: 8px;
}
</style>
