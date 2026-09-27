// 文件在线预览的类型分派（可复用）
// 取扩展名 → 预览类别：image / pdf / audio / video / markdown / text / word / html / none

const EXT_KIND_MAP = {
  // 图片（tif/tiff 走后端转 PNG 预览）
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image',
  bmp: 'image', tif: 'image-png', tiff: 'image-png',
  // PDF
  pdf: 'pdf',
  // 音频
  mp3: 'audio', wav: 'audio', m4a: 'audio', ogg: 'audio', flac: 'audio', amr: 'audio',
  // 视频
  mp4: 'video', webm: 'video', mov: 'video', avi: 'video', mkv: 'video',
  // Markdown
  md: 'markdown', markdown: 'markdown',
  // Word
  docx: 'word',
  // Excel（前端 xlsx 库渲染表格）
  xlsx: 'excel', xls: 'excel', csv: 'excel',
  // Office 文本化预览（后端提取正文：doc/ppt/pptx）
  doc: 'office-text', ppt: 'office-text', pptx: 'office-text',
  // HTML（沙箱 iframe 渲染）
  html: 'html', htm: 'html',
  // 纯文本
  txt: 'text', json: 'text', log: 'text', xml: 'text', yaml: 'text', yml: 'text',
  // 邮件（eml 按文本预览源码；msg 为二进制不支持预览）
  eml: 'text',
}

export function fileExt(fileName = '', fileType = '') {
  const fromName = fileName.includes('.') ? fileName.split('.').pop() : ''
  return (fromName || fileType || '').toLowerCase()
}

export function previewKind(fileName, fileType) {
  return EXT_KIND_MAP[fileExt(fileName, fileType)] || 'none'
}

export function canPreview(fileName, fileType) {
  return previewKind(fileName, fileType) !== 'none'
}

// 文本类文件超过该大小不再内联展示，提示下载
export const TEXT_PREVIEW_LIMIT = 2 * 1024 * 1024 // 2MB
