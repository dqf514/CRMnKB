<template>
  <div>
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
} from '../../api'

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

onMounted(load)
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
</style>
