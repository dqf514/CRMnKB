// 上传格式过滤工具：与后端"系统设置 → 解析文件格式"联动。
// 仅允许选择当前启用解析的格式；文件夹上传时自动排除不支持格式。
import { getUploadFormats } from '../api'

let cache = null // Set<string>：小写扩展名（含点），如 .pdf
let loading = null

export function ensureUploadFormats(force = false) {
  if (cache && !force) return Promise.resolve(cache)
  if (loading) return loading
  loading = getUploadFormats()
    .then((res) => {
      cache = new Set((res?.enabled || []).map((e) => String(e).toLowerCase()))
      return cache
    })
    .catch(() => {
      cache = null // 拉取失败时不过滤（保持可上传），下次再试
      return null
    })
    .finally(() => {
      loading = null
    })
  return loading
}

export function isEnabledExt(filename) {
  const name = String(filename || '')
  const i = name.lastIndexOf('.')
  if (i < 0) return false
  const ext = name.slice(i).toLowerCase()
  // 清单未加载完成时先放行，避免误拦
  return cache ? cache.has(ext) : true
}

export function enabledAcceptStr() {
  return cache ? [...cache].join(',') : ''
}
