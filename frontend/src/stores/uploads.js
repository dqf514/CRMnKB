import { defineStore } from 'pinia'
import { uploadLibraryFile } from '../api/libraryUpload'
import { getPstProgress } from '../api'

let uid = 0

// 全局上传队列：逐文件顺序上传，每文件实时进度（axios onUploadProgress），
// 侧边栏面板（UploadProgressPanel）展示总体进度 + 逐文件状态。
export const useUploadsStore = defineStore('uploads', {
  state: () => ({
    items: [], // {id, name, path, size, status, transferred, total, error, folder_id, customer_id, kb_ids, file}
    running: false,
    stopped: false,
    panelOpen: false,
  }),
  getters: {
    activeCount: (s) => s.items.filter((i) => i.status === 'queue' || i.status === 'uploading' || i.status === 'processing').length,
    doneCount: (s) => s.items.filter((i) => i.status === 'done').length,
    failedCount: (s) => s.items.filter((i) => i.status === 'error').length,
    skippedCount: (s) => s.items.filter((i) => i.status === 'skipped').length,
    overallTotal: (s) => s.items.reduce((a, i) => a + (i.size || 0), 0),
    overallLoaded: (s) => s.items.reduce((a, i) => a + (i.transferred || 0), 0),
    overallPercent: (s) => {
      const t = s.items.reduce((a, i) => a + (i.size || 0), 0)
      return t ? Math.min(100, Math.round((s.items.reduce((a, i) => a + (i.transferred || 0), 0) / t) * 100)) : 0
    },
    allFinished: (s) =>
      s.items.length > 0 && s.items.every((i) => ['done', 'skipped', 'error'].includes(i.status)),  },
  actions: {
    // 加入上传队列并开始顺序上传；文件与目录结构（paths）一并保留
    start({ files, paths, folder_id, customer_id, kb_ids }) {
      const list = Array.from(files || [])
      if (!list.length) return
      list.forEach((file, idx) => {
        this.items.push({
          id: ++uid,
          file,
          path: paths ? paths[idx] : undefined,
          name: file.name,
          size: file.size || 0,
          status: 'queue',
          transferred: 0,
          total: file.size || 0,
          error: '',
          folder_id,
          customer_id,
          kb_ids: kb_ids?.length ? kb_ids : undefined,
        })
      })
      this.stopped = false
      this.panelOpen = true
      this._run()
    },
    async _run() {
      if (this.running) return
      this.running = true
      try {
        for (const item of this.items) {
          if (item.status !== 'queue') continue
          if (this.stopped) { item.status = 'skipped'; item.error = '已停止'; continue }
          item.status = 'uploading'
          item.transferred = 0
          item.total = item.size
          try {
            const res = await uploadLibraryFile({
              file: item.file,
              path: item.path,
              folder_id: item.folder_id,
              customer_id: item.customer_id,
              kb_ids: item.kb_ids,
              onProgress: (e) => {
                if (e?.total) item.total = e.total
                item.transferred = e?.loaded || 0
              },
            })
            if (res?.skipped > 0) {
              item.status = 'skipped'
              item.error = (res.skipped_files && res.skipped_files[0]) || '已跳过'
            } else {
              item.status = 'done'
              item.transferred = item.total
              // PST 邮件归档：上传完成≠可用，进入后台拆解阶段，轮询解析进度
              const fid = res?.files?.[0]?.id
              if (fid && item.name.toLowerCase().endsWith('.pst')) {
                item.status = 'processing'
                item.file_id = fid
                item.pst = { done: 0, total: null }
                this._pollPst(item)
              }
            }
          } catch (err) {
            item.status = 'error'
            const detail = err?.response?.data?.detail
            item.error = typeof detail === 'string' ? detail : '上传失败'
          }
        }
      } finally {
        this.running = false
      }
    },
    retry(item) {
      item.status = 'queue'
      item.error = ''
      item.transferred = 0
      this.stopped = false
      this._run()
    },
    // PST 拆解进度轮询（每 3s，完成/失败即止）
    _pollPst(item) {
      const timer = setInterval(async () => {
        try {
          const p = await getPstProgress(item.file_id)
          item.pst = { done: p.done || 0, total: p.total }
          if (p.state === 'done') {
            item.status = 'done'
            item.pstText = `已拆出 ${p.done} 封邮件`
            clearInterval(timer)
          } else if (p.state === 'failed') {
            item.status = 'error'
            item.error = p.error || 'PST 解析失败'
            clearInterval(timer)
          }
        } catch { /* 下轮再试 */ }
      }, 3000)
      item._pstTimer = timer
    },
    stop() {
      this.stopped = true
    },
    clearFinished() {
      this.items = this.items.filter((i) => i.status === 'queue' || i.status === 'uploading')
      if (!this.items.length) this.panelOpen = false
    },
    close() {
      this.panelOpen = false
    },
  },
})
