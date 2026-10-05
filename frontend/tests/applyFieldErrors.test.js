import { describe, it, expect } from 'vitest'
import { applyFieldErrors } from '../src/utils/applyFieldErrors.js'

function err422(detail) {
  return { response: { status: 422, data: { detail } } }
}

// 模拟 el-form：fields 是 form-item 上下文数组
function fakeForm(...props) {
  return { fields: props.map((prop) => ({ prop, validateState: '', validateMessage: '' })) }
}

describe('applyFieldErrors', () => {
  it('非 422 / detail 非数组：返回 false 不消费', () => {
    const form = fakeForm('name')
    expect(applyFieldErrors({ response: { status: 500, data: {} } }, form)).toBe(false)
    expect(applyFieldErrors({ response: { status: 422, data: { detail: 'oops' } } }, form)).toBe(false)
    expect(applyFieldErrors(new Error('network'), form)).toBe(false)
  })

  it('loc 末段映射到字段，就地写错误态', () => {
    const form = fakeForm('name', 'phone')
    const applied = applyFieldErrors(err422([
      { loc: ['body', 'phone'], msg: 'Field required' },
    ]), form)
    expect(applied).toBe(true)
    const phone = form.fields[1]
    expect(phone.validateState).toBe('error')
    expect(phone.validateMessage).toBe('该字段为必填项')
    expect(form.fields[0].validateState).toBe('')
  })

  it('fieldMap 换算后端字段名到表单 prop', () => {
    const form = fakeForm('customer_id')
    const applied = applyFieldErrors(err422([
      { loc: ['body', 'customer_name'], msg: 'Field required' },
    ]), form, { customer_name: 'customer_id' })
    expect(applied).toBe(true)
    expect(form.fields[0].validateState).toBe('error')
  })

  it('字段不在表单里：跳过该条，全部不匹配则返回 false', () => {
    const form = fakeForm('name')
    expect(applyFieldErrors(err422([{ loc: ['body', 'ghost'], msg: 'x' }]), form)).toBe(false)
  })

  it('formRef 缺失时安全返回 false', () => {
    expect(applyFieldErrors(err422([{ loc: ['body', 'name'], msg: 'x' }]), null)).toBe(false)
  })

  it('常见 pydantic 英文消息翻成中文', () => {
    const form = fakeForm('a', 'b', 'c', 'd', 'e', 'f')
    applyFieldErrors(err422([
      { loc: ['body', 'a'], msg: 'String should have at least 8 characters' },
      { loc: ['body', 'b'], msg: 'String should have at most 20 characters' },
      { loc: ['body', 'c'], msg: 'Input should be greater than or equal to 0' },
      { loc: ['body', 'd'], msg: 'Input should be less than or equal to 100' },
      { loc: ['body', 'e'], msg: 'Input should be a valid email address, invalid character' },
      { loc: ['body', 'f'], msg: 'Some unknown error' },
    ]), form)
    const msgs = form.fields.map((f) => f.validateMessage)
    expect(msgs).toEqual([
      '至少输入 8 个字符',
      '最多输入 20 个字符',
      '不能小于 0',
      '不能大于 100',
      '邮箱格式不正确',
      'Some unknown error', // 未匹配的保留原文
    ])
  })
})
