<template>
  <div>
    <div class="stats-toolbar">
      <el-radio-group v-model="days" @change="loadStats">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
      </el-radio-group>
      <el-button :icon="Refresh" circle @click="loadStats" />
    </div>

    <!-- 顶部统计卡 -->
    <el-row :gutter="16" v-loading="loading">
      <el-col v-for="c in statCards" :key="c.label" :xs="12" :lg="6">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value tnum">{{ c.value }}</div>
          <div class="stat-sub">{{ c.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="card-title">调用趋势</span></template>
          <div ref="trendRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="card-title">按模型占比（调用次数）</span></template>
          <div ref="modelRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="card-title">按来源</span></template>
          <div ref="callerRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="card-title">最近失败</span></template>
          <el-table :data="stats.recent_failures" size="small" max-height="300">
            <el-table-column prop="model" label="模型" min-width="110" show-overflow-tooltip />
            <el-table-column prop="caller" label="来源" width="100" show-overflow-tooltip />
            <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
            <el-table-column label="时间" width="140">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!stats.recent_failures?.length" description="暂无失败记录" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getLlmStats } from '../../api'
import { useChart } from '../../utils/useChart'
import { formatDateTime } from '../../utils/format'

const days = ref(7)
const loading = ref(false)
const stats = reactive({
  total_calls: 0, success_rate: 0, avg_latency_ms: 0, total_tokens: 0,
  by_model: [], by_day: [], by_caller: [], recent_failures: [],
})

const trendRef = ref()
const modelRef = ref()
const callerRef = ref()
const trendChart = useChart(trendRef)
const modelChart = useChart(modelRef)
const callerChart = useChart(callerRef)

const statCards = computed(() => [
  { label: '总调用', value: (stats.total_calls ?? 0).toLocaleString(), sub: `近 ${days.value} 天` },
  { label: '成功率', value: `${Math.round((stats.success_rate ?? 0) * 100)}%`, sub: '调用成功占比' },
  { label: '平均延迟', value: `${Math.round(stats.avg_latency_ms ?? 0)} ms`, sub: '单次调用耗时' },
  { label: '总 Tokens', value: (stats.total_tokens ?? 0).toLocaleString(), sub: '输入 + 输出' },
])

function renderCharts() {
  const dates = stats.by_day.map((d) => d.date)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['调用次数', 'Tokens'] },
    grid: { left: 50, right: 60, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: dates },
    yAxis: [
      { type: 'value', name: '次数' },
      { type: 'value', name: 'Tokens', splitLine: { show: false } },
    ],
    series: [
      { name: '调用次数', type: 'line', smooth: true, data: stats.by_day.map((d) => d.calls), areaStyle: { opacity: 0.12 } },
      { name: 'Tokens', type: 'line', smooth: true, yAxisIndex: 1, data: stats.by_day.map((d) => d.tokens) },
    ],
  })
  modelChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 次（{d}%）' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['50%', '46%'],
      itemStyle: { borderRadius: 6, borderWidth: 2, borderColor: 'transparent' },
      label: { formatter: '{b}' },
      data: stats.by_model.map((m) => ({ name: m.model, value: m.calls })),
    }],
  })
  callerChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: { type: 'category', data: stats.by_caller.map((c) => c.caller), axisLabel: { rotate: 20 } },
    yAxis: { type: 'value' },
    series: [{
      type: 'bar',
      barMaxWidth: 40,
      itemStyle: { borderRadius: [6, 6, 0, 0] },
      data: stats.by_caller.map((c) => c.calls),
    }],
  })
}

async function loadStats() {
  loading.value = true
  try {
    const res = await getLlmStats({ days: days.value })
    Object.assign(stats, {
      total_calls: res?.total_calls ?? 0,
      success_rate: res?.success_rate ?? 0,
      avg_latency_ms: res?.avg_latency_ms ?? 0,
      total_tokens: res?.total_tokens ?? 0,
      by_model: res?.by_model || [],
      by_day: res?.by_day || [],
      by_caller: res?.by_caller || [],
      recent_failures: res?.recent_failures || [],
    })
    renderCharts()
  } finally {
    loading.value = false
  }
}

onMounted(loadStats)
</script>

<style scoped>
.stats-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.stat-card {
  margin-bottom: 16px;
}
.stat-label {
  font-size: 13px;
  color: var(--app-ink-2);
}
.stat-value {
  font-size: 26px;
  font-weight: 700;
  margin-top: 6px;
}
.stat-sub {
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 4px;
}
.card-title {
  font-weight: 600;
}
.chart {
  height: 300px;
  width: 100%;
}
</style>
