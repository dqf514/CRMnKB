<template>
  <div>
    <el-tabs v-model="activeTab">
      <el-tab-pane label="插件库" name="plugins">
    <el-card>
      <div class="toolbar">
        <span class="hint">
          dsh agent 运行时的插件库：启停/安装对全租户生效，保存后 dsh 进程自动重建（会话历史不丢）。
        </span>
        <el-input
          v-model="keyword"
          placeholder="搜索插件 id / 包名"
          clearable
          style="width: 220px; margin-left: auto"
          :prefix-icon="Search"
        />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button :icon="RefreshRight" @click="handleRestart">重建进程</el-button>
        <el-button type="primary" :icon="Plus" @click="installDialog = true">安装插件</el-button>
      </div>

      <el-alert
        v-if="loadError"
        type="warning"
        :closable="false"
        :title="loadError"
        style="margin-bottom: 12px"
      />

      <el-table :data="filtered" v-loading="loading" stripe>
        <el-table-column label="插件" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="plugin-id">{{ row.id }}</span>
            <el-tag v-if="row.custom" size="small" type="warning" effect="plain" style="margin-left: 6px">自装</el-tag>
            <el-tag v-if="row.overridden" size="small" type="primary" effect="plain" style="margin-left: 6px">已覆盖</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="包" min-width="220" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.name || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="来源" min-width="180" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.bundle || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.effective_disabled === null"
              :content="`动态表达式：${row.disabled_expr}（随运行上下文求值）`"
              placement="top"
            >
              <el-tag size="small" type="warning" effect="plain">动态</el-tag>
            </el-tooltip>
            <el-tag v-else size="small" :type="row.effective_disabled ? 'info' : 'success'">
              {{ row.effective_disabled ? '已禁用' : '启用中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch
              :model-value="row.effective_disabled === false"
              :loading="toggling === row.id"
              @change="(val) => toggle(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.custom"
              link
              type="danger"
              size="small"
              @click="handleRemoveCustom(row)"
            >停用</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !loadError && !filtered.length" description="没有匹配的插件" :image-size="80" />
    </el-card>
      </el-tab-pane>

      <el-tab-pane label="工具权限" name="tools">
    <el-card v-loading="toolLoading">
      <div class="toolbar">
        <span class="hint">
          agent（dsh）调用工具时的自动放行白名单：名单内自动执行，名单外一律拒绝。保存后最长 1 分钟内全量生效。
        </span>
        <el-button :icon="Refresh" style="margin-left: auto" @click="loadTools">刷新</el-button>
        <el-button type="primary" :loading="toolSaving" :disabled="!toolDirty" @click="saveTools">保存</el-button>
      </div>

      <el-alert type="warning" :closable="false" style="margin-bottom: 14px">
        写操作（新建/修改/删除客户等）正常走「Agent 审批」链；把高危工具加入白名单等于<strong>绕开审批直接执行</strong>，请谨慎开启。
      </el-alert>

      <div v-for="group in groupedCatalog" :key="group.source" class="tool-group">
        <div class="tool-group-title">{{ group.source }}</div>
        <div v-for="tool in group.items" :key="group.source + '/' + tool.name" class="tool-row">
          <div class="tool-info">
            <span class="tool-label">{{ tool.label }}</span>
            <span class="tool-name">{{ tool.name }}</span>
          </div>
          <el-tag v-if="tool.risk === 'high'" size="small" type="danger" effect="plain">高危</el-tag>
          <el-tag v-else-if="tool.risk === 'medium'" size="small" type="warning" effect="plain">中危</el-tag>
          <el-tag v-else size="small" type="success" effect="plain">只读</el-tag>
          <el-switch :model-value="toolEnabled(tool.name)" @change="(val) => toggleTool(tool, val)" />
        </div>
      </div>

      <div class="tool-group">
        <div class="tool-group-title">自定义工具</div>
        <div v-for="name in customTools" :key="name" class="tool-row">
          <div class="tool-info">
            <span class="tool-name">{{ name }}</span>
          </div>
          <el-tag size="small" type="info" effect="plain">自定义</el-tag>
          <el-button link type="danger" size="small" @click="removeCustomTool(name)">移除</el-button>
        </div>
        <div class="tool-add">
          <el-input
            v-model="newToolName"
            placeholder="工具名（字母/数字/_.-，MCP 前缀工具填 __ 末段即可）"
            clearable
            style="max-width: 360px"
            @keyup.enter="addCustomTool"
          />
          <el-button :disabled="!newToolName.trim()" @click="addCustomTool">添加</el-button>
        </div>
      </div>
    </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 安装插件 -->
    <el-dialog v-model="installDialog" title="安装 dsh 插件" width="min(92vw, 560px)" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="包名/路径">
          <el-input
            v-model="installSpec"
            placeholder="@scope/name[@version] 或本地绝对路径"
            clearable
          />
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" style="margin-bottom: 8px">
        <p>安装经 <code>dsh plugin --profile acp add</code> 转发 pnpm 执行，可能需要几分钟。</p>
        <p>装好后会自动加入覆盖层加载并重建 dsh 进程；不兼容的插件会被 dsh 启动校验拒绝。</p>
      </el-alert>
      <el-alert
        v-if="installResult"
        :type="installResult.ok ? 'success' : 'error'"
        :closable="false"
        :title="installResult.title"
      >
        <pre v-if="installResult.output" class="install-output">{{ installResult.output }}</pre>
      </el-alert>
      <template #footer>
        <el-button @click="installDialog = false">关闭</el-button>
        <el-button type="primary" :loading="installing" :disabled="!installSpec.trim()" @click="handleInstall">
          安装
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Plus, Refresh, RefreshRight, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getDshPlugins, toggleDshPlugin, installDshPlugin, removeDshCustomPlugin, restartDsh,
  getAgentToolWhitelist, setAgentToolWhitelist,
} from '../../api'

const activeTab = ref('plugins')

const loading = ref(false)
const loadError = ref('')
const list = ref([])
const keyword = ref('')
const toggling = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((it) =>
    it.id.toLowerCase().includes(kw) || (it.name || '').toLowerCase().includes(kw))
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await getDshPlugins()
    list.value = res?.items || []
  } catch (e) {
    // 503（agent 未启用/未配置 DSH_BIN）等由拦截器 toast，这里给页面级提示
    loadError.value = e?.response?.data?.detail || '插件清单加载失败'
    list.value = []
  } finally {
    loading.value = false
  }
}

async function toggle(row, enabled) {
  toggling.value = row.id
  try {
    await toggleDshPlugin(row.id, enabled)
    row.effective_disabled = !enabled
    row.overridden = true
    ElMessage.success(`已${enabled ? '启用' : '禁用'}「${row.id}」，dsh 进程已重建`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    toggling.value = ''
  }
}

async function handleRestart() {
  const ok = await ElMessageBox.confirm(
    '将关闭当前 dsh 进程，下次 agent 对话时自动重建并加载最新配置（会话历史不丢）。继续？',
    '重建 dsh 进程',
    { type: 'warning' },
  ).then(() => true).catch(() => false)
  if (!ok) return
  await restartDsh()
  ElMessage.success('dsh 进程已重建')
}

async function handleRemoveCustom(row) {
  const ok = await ElMessageBox.confirm(
    `停用自定义插件「${row.id}」？（移除加载条目，已安装包保留在 profile）`,
    '停用插件',
    { type: 'warning' },
  ).then(() => true).catch(() => false)
  if (!ok) return
  await removeDshCustomPlugin(row.id)
  ElMessage.success('已停用并重建 dsh 进程')
  load()
}

const installDialog = ref(false)
const installSpec = ref('')
const installing = ref(false)
const installResult = ref(null)

async function handleInstall() {
  installing.value = true
  installResult.value = null
  try {
    const res = await installDshPlugin(installSpec.value.trim())
    installResult.value = {
      ok: true,
      title: res.already ? `「${res.package}」已在加载列表中` : `「${res.package}」安装成功，dsh 进程已重建`,
      output: res.output,
    }
    installSpec.value = ''
    load()
  } catch (e) {
    installResult.value = { ok: false, title: e?.response?.data?.detail || '安装失败', output: '' }
  } finally {
    installing.value = false
  }
}

// ---- 工具权限白名单（agent 自动放行清单，整体替换保存）----
const toolLoading = ref(false)
const toolSaving = ref(false)
const toolCatalog = ref([])
const toolWhitelist = ref([])
const toolOriginal = ref('')
const newToolName = ref('')

const toolDirty = computed(() => JSON.stringify(toolWhitelist.value) !== toolOriginal.value)

const groupedCatalog = computed(() => {
  // 按来源分组（保持 catalog 顺序）
  const groups = []
  const bySource = new Map()
  for (const t of toolCatalog.value) {
    if (!bySource.has(t.source)) {
      const g = { source: t.source, items: [] }
      bySource.set(t.source, g)
      groups.push(g)
    }
    bySource.get(t.source).items.push(t)
  }
  return groups
})

const catalogNames = computed(() => new Set(toolCatalog.value.map((t) => t.name)))
// 白名单里不在已知目录中的条目（管理员手工加的）
const customTools = computed(() => toolWhitelist.value.filter((n) => !catalogNames.value.has(n)))

function toolEnabled(name) {
  return toolWhitelist.value.includes(name)
}

async function toggleTool(tool, enabled) {
  if (enabled && tool.risk === 'high') {
    const ok = await ElMessageBox.confirm(
      `「${tool.label}（${tool.name}）」是高危工具：加入白名单后 agent 将不经审批直接执行。确认开启？`,
      '高危工具确认',
      { type: 'error', confirmButtonText: '仍要开启', cancelButtonText: '取消' },
    ).then(() => true).catch(() => false)
    if (!ok) return
  }
  const idx = toolWhitelist.value.indexOf(tool.name)
  if (enabled && idx === -1) toolWhitelist.value.push(tool.name)
  if (!enabled && idx !== -1) toolWhitelist.value.splice(idx, 1)
}

function addCustomTool() {
  const name = newToolName.value.trim()
  if (!name) return
  if (!/^[A-Za-z0-9_.-]{1,100}$/.test(name)) {
    ElMessage.error('工具名仅支持字母/数字/_.-，最长 100 字符')
    return
  }
  if (toolWhitelist.value.includes(name)) {
    ElMessage.warning('已在白名单中')
    return
  }
  toolWhitelist.value.push(name)
  newToolName.value = ''
}

function removeCustomTool(name) {
  const idx = toolWhitelist.value.indexOf(name)
  if (idx !== -1) toolWhitelist.value.splice(idx, 1)
}

async function loadTools() {
  toolLoading.value = true
  try {
    const res = await getAgentToolWhitelist()
    toolCatalog.value = res?.catalog || []
    toolWhitelist.value = [...(res?.whitelist || [])]
    toolOriginal.value = JSON.stringify(res?.whitelist || [])
  } catch {
    /* 拦截器已提示 */
  } finally {
    toolLoading.value = false
  }
}

async function saveTools() {
  toolSaving.value = true
  try {
    const res = await setAgentToolWhitelist(toolWhitelist.value)
    toolWhitelist.value = [...(res?.whitelist || [])]
    toolOriginal.value = JSON.stringify(res?.whitelist || [])
    ElMessage.success('白名单已保存（最长 1 分钟内全量生效）')
  } catch {
    /* 拦截器已提示 */
  } finally {
    toolSaving.value = false
  }
}

onMounted(() => {
  load()
  loadTools()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.hint {
  font-size: 13px;
  color: var(--app-ink-2);
}
.muted {
  font-size: 12.5px;
  color: var(--app-ink-2);
}
.plugin-id {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
}
.install-output {
  margin: 6px 0 0;
  max-height: 200px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.tool-group {
  margin-bottom: 18px;
}
.tool-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-ink);
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--app-border, #e4e7ed);
}
.tool-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 4px;
}
.tool-row:hover {
  background: var(--app-bg-hover, #f5f7fa);
  border-radius: 6px;
}
.tool-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.tool-label {
  font-size: 13.5px;
}
.tool-name {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--app-ink-2);
}
.tool-add {
  display: flex;
  gap: 8px;
  padding: 7px 4px;
}
</style>
