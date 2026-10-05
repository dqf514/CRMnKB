import { describe, it, expect, vi } from 'vitest'
import { validateForm } from '../src/utils/validateForm.js'

describe('validateForm', () => {
  it('formRef 为空时直接放行（true）', async () => {
    expect(await validateForm(null)).toBe(true)
    expect(await validateForm(undefined)).toBe(true)
  })

  it('校验通过（resolve）返回 true', async () => {
    const formRef = { validate: vi.fn(async () => true) }
    expect(await validateForm(formRef)).toBe(true)
  })

  it('校验失败（reject）吞掉 rejection 返回 false', async () => {
    const formRef = { validate: vi.fn(async () => { throw new Error('invalid') }) }
    expect(await validateForm(formRef)).toBe(false)
  })
})
