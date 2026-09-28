<template>
  <el-drawer
    :model-value="modelValue"
    :title="form.id ? '编辑客户' : '新增客户'"
    size="min(92vw, 460px)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form :model="form" :rules="formRules" ref="formRef" label-width="80px">
      <el-form-item label="名称" prop="name">
        <el-input v-model="form.name" placeholder="客户名称" @blur="checkDuplicates" />
      </el-form-item>
      <el-form-item label="单位">
        <el-input v-model="form.company" placeholder="所属单位 / 公司" />
      </el-form-item>
      <el-form-item label="职务">
        <el-input v-model="form.position" placeholder="联系人职务" />
      </el-form-item>
      <el-form-item label="行业">
        <el-select
          v-model="form.industries"
          multiple
          filterable
          style="width: 100%"
          placeholder="请选择行业（可多选）"
          @visible-change="(v) => v && industryStore.load()"
        >
          <el-option v-for="i in industryStore.list" :key="i.id" :label="i.name" :value="i.name" />
        </el-select>
      </el-form-item>
      <el-form-item label="标签">
        <el-select
          v-model="form.tags"
          multiple
          filterable
          allow-create
          default-first-option
          style="width: 100%"
          placeholder="选择或输入标签，回车创建"
        >
          <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
        </el-select>
      </el-form-item>
      <el-form-item label="电话" prop="phone">
        <el-input v-model="form.phone" placeholder="联系电话" @blur="checkDuplicates" />
      </el-form-item>
      <div v-if="dupList.length" class="dup-tip">
        <el-alert type="warning" :closable="false" show-icon title="疑似重复客户，点击查看详情：">
          <div v-for="d in dupList" :key="d.id" class="dup-item">
            <el-link type="warning" @click="goCustomer(d.id)">{{ d.name }}<template v-if="d.company">（{{ d.company }}）</template><template v-if="d.phone"> · {{ d.phone }}</template></el-link>
          </div>
        </el-alert>
      </div>
      <el-form-item label="微信">
        <el-input v-model="form.wechat" placeholder="微信号" />
      </el-form-item>
      <el-form-item label="邮箱" prop="email">
        <el-input v-model="form.email" placeholder="电子邮箱" />
      </el-form-item>
      <el-form-item label="地址">
        <el-input v-model="form.address" placeholder="联系地址" />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="form.status" style="width: 100%">
          <el-option v-for="(v, k) in customerStatusMap" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="来源">
        <el-input v-model="form.source" placeholder="客户来源" />
      </el-form-item>
      <el-form-item label="生日">
        <el-date-picker v-model="form.birthday" type="date" value-format="YYYY-MM-DD" style="width: 100%" placeholder="客户生日" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
    </template>
  </el-drawer>
</template>

<script setup>
// 客户新增/编辑抽屉：列表页「新增客户」与详情页「编辑」共用
import { ref, reactive, computed, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createCustomer, updateCustomer, getCustomerDuplicates } from '../api'
import { useIndustryStore } from '../stores/industries'
import { customerStatusMap, CUSTOMER_TAG_PRESETS } from '../utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 传入客户对象=编辑；null=新增
  customer: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const router = useRouter()
const industryStore = useIndustryStore()
const saving = ref(false)
const formRef = ref()

const emptyForm = {
  id: null, name: '', company: '', position: '', wechat: '',
  industries: [], tags: [], phone: '', email: '', address: '',
  status: 'potential', source: '', birthday: '',
}
const form = reactive({ ...emptyForm })
const formRules = {
  name: [{ required: true, message: '请输入客户名称', trigger: 'blur' }],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  phone: [{ pattern: /^[\d+\-() ]{5,20}$/, message: '电话格式不正确', trigger: 'blur' }],
}

// 标签选项：预设 + 当前客户已有的自定义标签
const tagOptions = computed(() => {
  const set = new Set(CUSTOMER_TAG_PRESETS)
  ;(props.customer?.tags || []).forEach((t) => set.add(t))
  return [...set]
})

// 打开抽屉时初始化表单
watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    industryStore.load()
    const row = props.customer
    Object.assign(form, emptyForm, row ? {
      id: row.id,
      name: row.name,
      company: row.company || '',
      position: row.position || '',
      wechat: row.wechat || '',
      industries: [...(row.industries || [])],
      tags: [...(row.tags || [])],
      phone: row.phone,
      email: row.email,
      address: row.address,
      status: row.status,
      source: row.source,
      birthday: row.birthday || '',
    } : {})
    dupList.value = []
    nextTick(() => formRef.value?.clearValidate())
  }
)

// ========== 查重（姓名/电话失焦时触发） ==========
const dupList = ref([])

async function checkDuplicates() {
  const name = form.name?.trim()
  const phone = form.phone?.trim()
  if (!name && !phone) {
    dupList.value = []
    return
  }
  try {
    const params = {}
    if (name) params.name = name
    if (phone) params.phone = phone
    if (form.id) params.exclude_id = form.id
    const res = await getCustomerDuplicates(params)
    dupList.value = Array.isArray(res) ? res : []
  } catch {
    dupList.value = []
  }
}

function goCustomer(id) {
  router.push(`/customers/${id}`)
}

async function handleSave() {
  await formRef.value.validate()
  saving.value = true
  try {
    const data = {
      name: form.name,
      company: form.company || null,
      position: form.position || null,
      wechat: form.wechat || null,
      industries: form.industries,
      tags: form.tags,
      phone: form.phone,
      email: form.email,
      address: form.address,
      status: form.status,
      source: form.source,
      birthday: form.birthday || null,
    }
    if (form.id) {
      await updateCustomer(form.id, data)
      ElMessage.success('更新成功')
    } else {
      await createCustomer(data)
      ElMessage.success('创建成功')
    }
    emit('update:modelValue', false)
    emit('saved')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.dup-tip {
  margin: -6px 0 12px 80px;
}
.dup-item {
  line-height: 1.8;
}
</style>
