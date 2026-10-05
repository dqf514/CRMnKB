import { describe, it, expect, vi } from 'vitest'

// 隔离 element-plus，只验证「resolve→true / reject→false」的吞错语义
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: vi.fn() },
}))

import { ElMessageBox } from 'element-plus'
import { confirmDanger } from '../src/utils/confirmDanger.js'

describe('confirmDanger', () => {
  it('用户确认（resolve）返回 true', async () => {
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    expect(await confirmDanger('确定删除？')).toBe(true)
  })

  it('用户取消/关闭/Esc（reject）静默返回 false', async () => {
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    expect(await confirmDanger('确定删除？')).toBe(false)
  })

  it('透传自定义标题与选项', async () => {
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    await confirmDanger('msg', '清空确认', { confirmButtonText: '清空' })
    expect(ElMessageBox.confirm).toHaveBeenCalledWith('msg', '清空确认', {
      type: 'warning',
      confirmButtonText: '清空',
    })
  })
})
