// 通用表格导出 Excel（xlsx）。任列表页复用：定义列映射，一键导出当前数据。
import * as XLSX from 'xlsx'

/**
 * 把行数据导出为 .xlsx。
 * @param rows   数组，每项一个对象
 * @param columns [{key, label}] 要导出的列（key 取行字段，label 为中文表头）
 * @param filename 导出文件名（自动加时间戳）
 */
export function exportRowsToExcel(rows, columns, filename = '导出') {
  const cols = columns && columns.length ? columns : Object.keys(rows[0] || {}).map((k) => ({ key: k, label: k }))
  const sheetData = rows.map((r) => {
    const o = {}
    cols.forEach((c) => {
      let v = r[c.key]
      if (v instanceof Date) v = v.toISOString().slice(0, 10)
      o[c.label] = v ?? ''
    })
    return o
  })
  const ws = XLSX.utils.json_to_sheet(sheetData)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '数据')
  const ts = new Date().toISOString().slice(0, 10)
  XLSX.writeFile(wb, `${filename}_${ts}.xlsx`)
}
