// 文档库上传封装：手动构造 FormData，供文档库页与客户文档页签复用
// 单/多文件上传：files 为 File 数组；folderUpload 时 paths 取 webkitRelativePath 保留目录结构
import request from './index'

export function uploadLibraryFiles({ files, paths, folder_id, customer_id, kb_ids }) {
  const formData = new FormData()
  files.forEach((f) => formData.append('files', f))
  if (paths?.length) paths.forEach((p) => formData.append('paths', p))
  if (folder_id) formData.append('folder_id', folder_id)
  if (customer_id) formData.append('customer_id', customer_id)
  if (kb_ids?.length) kb_ids.forEach((id) => formData.append('kb_ids', id))
  return request.post('/api/v1/library/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
}

// 逐文件上传（上传进度面板用）：一次一个文件，实时回调 onProgress(loaded, total)
export function uploadLibraryFile({ file, path, folder_id, customer_id, kb_ids, onProgress }) {
  const formData = new FormData()
  formData.append('files', file)
  if (path) formData.append('paths', path)
  if (folder_id) formData.append('folder_id', folder_id)
  if (customer_id) formData.append('customer_id', customer_id)
  if (kb_ids?.length) kb_ids.forEach((id) => formData.append('kb_ids', id))
  return request.post('/api/v1/library/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
    onUploadProgress: onProgress,
  })
}

// 上传结果汇总文案
export function uploadSummary(res) {
  const uploaded = res?.uploaded ?? 0
  const supported = res?.supported ?? 0
  const unsupported = res?.unsupported ?? 0
  const skipped = res?.skipped ?? 0
  let msg = `上传完成：成功 ${uploaded} 个`
  if (supported) msg += `，可解析 ${supported} 个`
  if (unsupported) msg += `，仅存储 ${unsupported} 个`
  if (skipped) msg += `，跳过 ${skipped} 个`
  return msg
}
