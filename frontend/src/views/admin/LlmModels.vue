<template>
  <div>
    <el-card>
      <div class="toolbar">
        <span class="hint">配置对话（chat）、嵌入（embed）、视觉理解（vision）、语音识别（asr）、重排序（rerank）模型；设为默认后同类互斥</span>
        <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openForm()">新增模型</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column label="名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.is_default" size="small" type="danger" effect="plain" style="margin-left: 6px">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Provider" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="enumTagType(llmProviderMap, row.provider)">
              {{ enumLabel(llmProviderMap, row.provider) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="enumTagType(llmModelTypeMap, row.model_type)">
              {{ enumLabel(llmModelTypeMap, row.model_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="Model" min-width="130" show-overflow-tooltip />
        <el-table-column prop="base_url" label="Base URL" min-width="170" show-overflow-tooltip />
        <el-table-column label="Key" width="120">
          <template #default="{ row }">
            <span class="masked-key">{{ row.api_key_masked || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(val) => toggleEnabled(row, val)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openForm(row)">编辑</el-button>
            <el-button v-if="!row.is_default" link type="warning" size="small" @click="handleSetDefault(row)">设为默认</el-button>
            <el-button link type="success" size="small" :loading="testingId === row.id" @click="handleTest(row)">测试连接</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !list.length" description="暂无模型配置" :image-size="80" />
    </el-card>

    <!-- 新增 / 编辑模型 -->
    <el-drawer v-model="formDrawer" :title="form.id ? '编辑模型' : '新增模型'" size="min(92vw, 480px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="配置名称，如 DeepSeek 对话" />
        </el-form-item>
        <el-form-item label="Provider" prop="provider">
          <el-radio-group v-model="form.provider" @change="onProviderChange">
            <el-radio-button v-for="(v, k) in llmProviderMap" :key="k" :value="k">{{ v.label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="类型" prop="model_type">
          <el-radio-group v-model="form.model_type">
            <el-radio-button v-for="(v, k) in llmModelTypeMap" :key="k" :value="k">{{ v.label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Base URL" prop="base_url">
          <el-input v-model="form.base_url" :placeholder="baseUrlPlaceholder" @blur="fetchModels(true)" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="form.id ? '留空表示不修改' : 'API 密钥'"
            @blur="fetchModels(true)"
          />
        </el-form-item>
        <el-form-item label="Model" prop="model">
          <el-select
            v-model="form.model"
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            :loading="modelsLoading"
            :placeholder="modelPlaceholder"
          >
            <el-option v-for="m in remoteModels" :key="m" :label="m" :value="m" />
          </el-select>
          <div v-if="modelsFailed" class="field-tip">未能自动获取模型列表，可手动输入</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <p class="hint">视觉理解用于图片与扫描件识别（未配置时回退对话模型）；语音识别用于音视频转写；重排序用于知识库检索精排（未配置时回退对话模型打分）。</p>
      <template #footer>
        <el-button @click="formDrawer = false">取消</el-button>
        <el-button type="success" plain :loading="testingConfig" @click="handleTestConfig">测试</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { confirmDanger } from '../../utils/confirmDanger'
import { validateForm } from '../../utils/validateForm'
import {
  getLlmModels, createLlmModel, updateLlmModel, deleteLlmModel, setLlmModelDefault, testLlmModel,
  fetchRemoteModels, testLlmConfig,
} from '../../api'
import { llmProviderMap, llmModelTypeMap, enumLabel, enumTagType } from '../../utils/format'

const loading = ref(false)
const list = ref([])
const testingId = ref(null)

async function loadList() {
  loading.value = true
  try {
    const res = await getLlmModels()
    list.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    loading.value = false
  }
}

// ========== 表单 ==========
const formDrawer = ref(false)
const saving = ref(false)
const formRef = ref()
const emptyForm = {
  id: null, name: '', provider: 'api', model_type: 'chat',
  base_url: '', api_key: '', model: '', enabled: true,
}
const form = reactive({ ...emptyForm })
const formRules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  provider: [{ required: true, message: '请选择 Provider', trigger: 'change' }],
  model_type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  base_url: [
    { required: true, message: '请输入 Base URL', trigger: 'blur' },
    {
      validator: (r, v, cb) => {
        if (!v) return cb()
        try {
          const u = new URL(v)
          return ['http:', 'https:'].includes(u.protocol) ? cb() : cb(new Error('Base URL 必须是合法的 http(s) 地址'))
        } catch {
          cb(new Error('Base URL 必须是合法的 http(s) 地址'))
        }
      },
      trigger: 'blur',
    },
  ],
  model: [{ required: true, message: '请输入 Model', trigger: 'blur' }],
}

const baseUrlPlaceholder = computed(() =>
  form.provider === 'ollama' ? 'http://localhost:11434' : 'https://api.deepseek.com/v1'
)
const modelPlaceholder = computed(() => {
  if (form.model_type === 'embed') return '如 bge-m3'
  if (form.model_type === 'rerank') return '如 bge-reranker-v2-m3'
  return form.provider === 'ollama' ? '如 qwen2.5:7b' : '如 deepseek-chat'
})

// ========== 远程模型列表自动拉取 ==========
const remoteModels = ref([])
const modelsLoading = ref(false)
const modelsFailed = ref(false)

// needKey=true 时（失焦触发）api provider 需 base_url + api_key 齐备；ollama 仅需 base_url
async function fetchModels(needKey) {
  const baseUrl = form.base_url?.trim()
  if (!baseUrl) return
  if (needKey && form.provider === 'api' && !form.api_key?.trim()) return
  modelsLoading.value = true
  modelsFailed.value = false
  try {
    const res = await fetchRemoteModels({
      provider: form.provider,
      base_url: baseUrl,
      api_key: form.api_key?.trim() || undefined,
    })
    if (res?.ok && res.models?.length) {
      remoteModels.value = res.models
    } else {
      remoteModels.value = []
      modelsFailed.value = true
    }
  } catch {
    remoteModels.value = []
    modelsFailed.value = true
  } finally {
    modelsLoading.value = false
  }
}

// provider 切换后旧列表不再适用，清空按需重新拉取
function onProviderChange() {
  remoteModels.value = []
  modelsFailed.value = false
}

// ========== 表单内连通测试（未保存配置） ==========
const testingConfig = ref(false)

async function handleTestConfig() {
  if (!form.provider || !form.model_type || !form.base_url?.trim() || !form.model?.trim()) {
    ElMessage.warning('请先完整填写 Provider / 类型 / Base URL / Model')
    return
  }
  testingConfig.value = true
  try {
    const payload = {
      provider: form.provider,
      model_type: form.model_type,
      base_url: form.base_url.trim(),
      model: form.model.trim(),
    }
    // 留空 = 不修改：编辑态传空 key + model_id，后端用已保存的 key
    if (form.api_key) payload.api_key = form.api_key
    if (form.id) payload.model_id = form.id
    const res = await testLlmConfig(payload)
    if (res?.ok) {
      ElMessage.success(`连通正常，耗时 ${res.latency_ms ?? '-'} ms`)
    } else {
      ElMessage.error(res?.detail || '连接失败')
    }
  } finally {
    testingConfig.value = false
  }
}

function openForm(row) {
  remoteModels.value = []
  modelsFailed.value = false
  Object.assign(form, emptyForm, row ? {
    id: row.id,
    name: row.name,
    provider: row.provider,
    model_type: row.model_type,
    base_url: row.base_url,
    api_key: '',
    model: row.model,
    enabled: row.enabled,
  } : {})
  formDrawer.value = true
  nextTick(() => formRef.value?.clearValidate())
  // 编辑场景自动拉一次模型列表（api_key 为空时后端不带 Authorization，需 key 的服务会失败，
  // 用户填新 key 后失焦会再次触发）
  if (row) fetchModels(false)
}

async function handleSave() {
  if (!(await validateForm(formRef.value))) return
  saving.value = true
  try {
    const data = {
      name: form.name,
      provider: form.provider,
      model_type: form.model_type,
      base_url: form.base_url,
      model: form.model,
      enabled: form.enabled,
    }
    if (form.id) {
      // api_key 传空 = 不修改
      if (form.api_key) data.api_key = form.api_key
      await updateLlmModel(form.id, data)
      ElMessage.success('更新成功')
    } else {
      if (form.api_key) data.api_key = form.api_key
      await createLlmModel(data)
      ElMessage.success('创建成功')
    }
    formDrawer.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, val) {
  await updateLlmModel(row.id, { enabled: val })
  row.enabled = val
  ElMessage.success(val ? '已启用' : '已停用')
}

async function handleSetDefault(row) {
  await setLlmModelDefault(row.id)
  ElMessage.success(`已将「${row.name}」设为默认`)
  loadList()
}

async function handleTest(row) {
  testingId.value = row.id
  try {
    const res = await testLlmModel(row.id)
    if (res?.ok) {
      ElMessage.success(`连接成功，延迟 ${res.latency_ms ?? '-'} ms`)
    } else {
      ElMessage.error(`连接失败：${res?.detail || '未知原因'}`)
    }
  } finally {
    testingId.value = null
  }
}

async function handleDelete(row) {
  const ok = await confirmDanger(`确定删除模型配置「${row.name}」吗？`)
  if (!ok) return
  await deleteLlmModel(row.id)
  ElMessage.success('删除成功')
  loadList()
}

onMounted(loadList)
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 16px;
}
.hint {
  color: var(--app-ink-2);
  font-size: 13px;
}
.masked-key {
  font-family: monospace;
  font-size: 12px;
  color: var(--app-ink-2);
}
.field-tip {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--app-ink-2);
}
</style>
