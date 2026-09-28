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

      <!-- 登录与接入：短信验证码通道 + 微信登录预留 -->
      <el-tab-pane label="登录与接入" name="login">
        <div class="hint formats-hint">
          手机号验证码登录：开启后登录页出现「手机验证码登录」入口，用户需先在「个人中心」绑定手机号；
          微信登录当前仅保存配置（users 表已预留 wechat_openid/unionid 字段），扫码/授权登录流程后续版本接入。
        </div>
        <el-form label-width="130px" style="max-width: 640px">
          <el-divider content-position="left">短信验证码登录</el-divider>
          <el-form-item label="启用短信登录">
            <el-switch v-model="loginCfg.sms.enabled" />
          </el-form-item>
          <el-form-item label="发送通道">
            <el-radio-group v-model="loginCfg.sms.provider">
              <el-radio value="log">仅记录日志（开发/内测）</el-radio>
              <el-radio value="http">通用 HTTP 网关</el-radio>
            </el-radio-group>
            <div class="hint" style="margin-left: 0; width: 100%">
              log 通道不真实发短信，dev 环境验证码直接返回到登录页；阿里云/腾讯云通道为预留扩展位，确定平台后即可接入。
            </div>
          </el-form-item>
          <template v-if="loginCfg.sms.provider === 'http'">
            <el-form-item label="网关 URL">
              <el-input v-model="loginCfg.sms.http.url" placeholder="https://sms-provider.example.com/send" />
            </el-form-item>
            <el-form-item label="请求体模板">
              <el-input
                v-model="loginCfg.sms.http.body_template"
                type="textarea" :rows="2"
                placeholder='{"phone": "{phone}", "code": "{code}"}'
              />
              <div class="hint" style="margin-left: 0; width: 100%">{phone} {code} 会被替换为实际值</div>
            </el-form-item>
            <el-form-item label="请求头">
              <el-input
                v-model="loginCfg.sms.http.headersText"
                type="textarea" :rows="2"
                placeholder='{"Authorization": "Bearer xxx"}（JSON，可空）'
              />
            </el-form-item>
          </template>
          <el-divider content-position="left">微信登录（预留）</el-divider>
          <el-form-item label="启用微信登录">
            <el-switch v-model="loginCfg.wechat.enabled" />
          </el-form-item>
          <el-form-item label="AppID">
            <el-input v-model="loginCfg.wechat.app_id" placeholder="微信开放平台 / 公众号 AppID" />
          </el-form-item>
          <el-form-item label="AppSecret">
            <el-input
              v-model="loginCfg.wechat.app_secret"
              type="password" show-password
              :placeholder="loginCfg.wechat.has_app_secret ? `已保存（尾号 ${loginCfg.wechat.app_secret_tail}），输入以更换` : '未设置'"
            />
            <div class="hint" style="margin-left: 0; width: 100%">加密存储、脱敏回显；留空表示不修改，输入新值后保存即覆盖</div>
          </el-form-item>
          <el-form-item label="回调地址">
            <el-input v-model="loginCfg.wechat.redirect_uri" placeholder="https://your-domain/login/wechat/callback" />
          </el-form-item>
        </el-form>
        <div class="footer">
          <el-button type="primary" :loading="saving" @click="saveLoginCfg">保存</el-button>
        </div>
      </el-tab-pane>
      <!-- 邮件写作规范：注入 AI 邮件草稿生成 -->
      <el-tab-pane label="邮件写作规范" name="email-guide">
        <div class="hint formats-hint">
          该规范会注入 AI 邮件草稿生成，约束邮件的写法、结构和风格
        </div>
        <el-input
          v-model="emailGuide"
          type="textarea"
          :rows="12"
          placeholder="例如：邮件开头称呼对方姓名；正文分三段：背景、方案、下一步；落款使用公司统一签名……"
          style="max-width: 720px"
        />
        <div class="footer">
          <el-button type="primary" :loading="saving" @click="saveEmailGuide">保存</el-button>
        </div>
      </el-tab-pane>
      <!-- 文档资料类型：客户文档上传/展示的可选分类，可增删改 -->
      <el-tab-pane label="文档资料类型" name="doc-categories">
        <div class="hint formats-hint">
          客户文档上传时可选的资料类型；标识用于存储（仅小写字母/数字/下划线），名称为界面显示。
          删除类型不影响存量文件（已标记的文件会显示原始标识）。
        </div>
        <el-table :data="docCategories" size="small" style="max-width: 720px">
          <el-table-column label="标识（value）" width="220">
            <template #default="{ row }">
              <el-input v-model="row.value" size="small" placeholder="如 factsheet" :disabled="!row._new" />
            </template>
          </el-table-column>
          <el-table-column label="显示名称">
            <template #default="{ row }">
              <el-input v-model="row.label" size="small" placeholder="如 产品资料" />
            </template>
          </el-table-column>
          <el-table-column width="70" align="center">
            <template #default="{ $index }">
              <el-button link type="danger" size="small" @click="docCategories.splice($index, 1)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="footer" style="display: flex; gap: 8px">
          <el-button size="small" @click="docCategories.push({ value: '', label: '', _new: true })">新增类型</el-button>
          <el-button type="primary" size="small" :loading="saving" @click="saveDocCategories">保存</el-button>
        </div>
      </el-tab-pane>
      <el-tab-pane label="系统更新" name="update" v-if="updateInfo.enabled">
        <div class="hint formats-hint">
          从 GitHub 拉取最新代码并自动重启后端（git pull → 按需安装依赖/构建前端 → 延迟重启）。
          更新期间服务会短暂中断（约十几秒），请在空闲时段操作。
        </div>
        <div class="update-row">
          <span class="update-current">当前版本：<code>{{ updateInfo.commit || '未知' }}</code></span>
          <el-button type="primary" :loading="updating" @click="handleSystemUpdate">
            检查并更新
          </el-button>
        </div>
        <pre v-if="updateOutput" class="update-output">{{ updateOutput }}</pre>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getBrand, updateBrand, uploadBrandLogo, getParseFormats, updateParseFormats, getLoginIntegrations, updateLoginIntegrations, getEmailGuide, updateEmailGuide, getDocCategories, updateDocCategories, getSystemUpdateInfo, checkSystemUpdate, runSystemUpdate } from '../../api'
import { useBrandStore } from '../../stores/brand'
import { useDocCategoryStore } from '../../stores/docCategories'

const brandStore = useBrandStore()
const docCategoryStore = useDocCategoryStore()

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

// ========== 登录与接入 ==========
const loginCfg = reactive({
  sms: {
    enabled: false,
    provider: 'log',
    http: { url: '', body_template: '{"phone": "{phone}", "code": "{code}"}', headersText: '' },
  },
  wechat: { enabled: false, app_id: '', app_secret: '', has_app_secret: false, app_secret_tail: '', redirect_uri: '' },
})

async function loadLoginCfg() {
  try {
    const res = await getLoginIntegrations()
    loginCfg.sms.enabled = !!res?.sms?.enabled
    loginCfg.sms.provider = res?.sms?.provider || 'log'
    loginCfg.sms.http.url = res?.sms?.http?.url || ''
    loginCfg.sms.http.body_template = res?.sms?.http?.body_template || '{"phone": "{phone}", "code": "{code}"}'
    loginCfg.sms.http.headersText = res?.sms?.http?.headers ? JSON.stringify(res.sms.http.headers, null, 2) : ''
    loginCfg.wechat.enabled = !!res?.wechat?.enabled
    loginCfg.wechat.app_id = res?.wechat?.app_id || ''
    loginCfg.wechat.has_app_secret = !!res?.wechat?.has_app_secret
    loginCfg.wechat.app_secret_tail = res?.wechat?.app_secret_tail || ''
    loginCfg.wechat.app_secret = ''
    loginCfg.wechat.redirect_uri = res?.wechat?.redirect_uri || ''
  } catch { /* 拦截器已提示 */ }
}

async function saveLoginCfg() {
  let headers = {}
  if (loginCfg.sms.provider === 'http' && loginCfg.sms.http.headersText.trim()) {
    try {
      headers = JSON.parse(loginCfg.sms.http.headersText)
    } catch {
      ElMessage.warning('请求头不是合法 JSON')
      return
    }
  }
  saving.value = true
  try {
    const res = await updateLoginIntegrations({
      sms: {
        enabled: loginCfg.sms.enabled,
        provider: loginCfg.sms.provider,
        http: { url: loginCfg.sms.http.url, body_template: loginCfg.sms.http.body_template, headers },
      },
      wechat: {
        enabled: loginCfg.wechat.enabled,
        app_id: loginCfg.wechat.app_id,
        // 未输入 = 保持原值（后端 null 语义）；输入后覆盖
        app_secret: loginCfg.wechat.app_secret || null,
        redirect_uri: loginCfg.wechat.redirect_uri,
      },
    })
    loginCfg.wechat.has_app_secret = !!res?.wechat?.has_app_secret
    loginCfg.wechat.app_secret_tail = res?.wechat?.app_secret_tail || ''
    loginCfg.wechat.app_secret = ''
    // 短信开关变化影响登录页入口，刷新公开品牌配置
    brandStore.load()
    ElMessage.success('已保存')
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// ========== 邮件写作规范 ==========
const emailGuide = ref('')

async function loadEmailGuide() {
  try {
    const res = await getEmailGuide()
    emailGuide.value = res?.guide || ''
  } catch { /* 拦截器已提示 */ }
}

async function saveEmailGuide() {
  saving.value = true
  try {
    await updateEmailGuide(emailGuide.value)
    ElMessage.success('已保存')
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// ========== 文档资料类型 ==========
const docCategories = ref([])

async function loadDocCategories() {
  try {
    const res = await getDocCategories()
    docCategories.value = (res?.items || []).map((c) => ({ value: c.value, label: c.label }))
  } catch { /* 拦截器已提示 */ }
}

async function saveDocCategories() {
  saving.value = true
  try {
    const items = docCategories.value.map((c) => ({ value: (c.value || '').trim(), label: (c.label || '').trim() }))
    await updateDocCategories(items)
    ElMessage.success('已保存')
    // 刷新全局缓存，上传/展示处立即生效
    docCategoryStore.loaded = false
    docCategoryStore.load(true)
    loadDocCategories()
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// ========== 公共 ==========
const activeTab = ref('brand')
const saving = ref(false)

// ========== 系统更新（裸机部署；UPDATE_SCRIPT 未配置时整个 tab 不显示） ==========
const updateInfo = ref({ enabled: false, commit: null })
const updating = ref(false)
const updateOutput = ref('')

async function loadUpdateInfo() {
  try {
    updateInfo.value = await getSystemUpdateInfo()
  } catch { /* 未配置或接口不可用时保持隐藏 */ }
}

async function handleSystemUpdate() {
  updating.value = true
  updateOutput.value = ''
  try {
    // 先检查远端是否有新提交：无更新直接提示，有更新再询问是否执行
    const chk = await checkSystemUpdate()
    if (!chk.ok) {
      ElMessage.error(chk.error || '检查更新失败（网络不可达？）')
      return
    }
    if (chk.commit) updateInfo.value.commit = chk.commit
    if (!chk.behind) {
      ElMessage.success('当前已是最新版本')
      return
    }
    await ElMessageBox.confirm(
      `发现 ${chk.behind} 个新提交${chk.latest ? `（最新：${chk.latest}）` : ''}。更新将从 GitHub 拉取代码并自动重启后端，期间服务短暂中断。是否现在更新？`,
      '发现新版本',
      { type: 'warning', confirmButtonText: '现在更新', cancelButtonText: '取消' },
    ).catch(() => Promise.reject(new Error('cancel')))
    const res = await runSystemUpdate()
    updateOutput.value = res.output || ''
    if (res.ok) {
      ElMessage.success('更新完成，后端正在重启，请稍后刷新页面')
      loadUpdateInfo()
    } else {
      ElMessage.error(`更新失败（退出码 ${res.exit_code}），详见下方日志`)
    }
  } catch {
    /* 取消或拦截器已提示 */
  } finally {
    updating.value = false
  }
}

onMounted(() => {
  loadBrand()
  loadFormats()
  loadLoginCfg()
  loadEmailGuide()
  loadDocCategories()
  loadUpdateInfo()
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
.update-row {
  display: flex;
  align-items: center;
  gap: 16px;
}
.update-current {
  font-size: 13px;
  color: var(--app-ink-2);
}
.update-current code {
  background: var(--app-bg);
  border: 1px solid var(--app-line);
  border-radius: 4px;
  padding: 1px 6px;
}
.update-output {
  margin-top: 14px;
  max-height: 360px;
  overflow: auto;
  background: var(--app-bg);
  border: 1px solid var(--app-line);
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--app-ink-2);
}
</style>
