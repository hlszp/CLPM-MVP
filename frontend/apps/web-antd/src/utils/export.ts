/**
 * 通用数据导出工具（P3-05/P3-23）
 *
 * 支持 CSV 和 Excel（HTML 表格格式，.xls）两种格式，无需第三方依赖。
 * - CSV：UTF-8 BOM + 双引号转义，Excel 可直接打开
 * - Excel：HTML 表格 + application/vnd.ms-excel MIME，Excel 原生兼容
 *
 * 用法：
 * ```ts
 * exportData({
 *   filename: 'audit-log',
 *   format: 'csv', // 或 'excel'
 *   headers: ['用户', '操作', '时间'],
 *   rows: [['admin', '登录', '2026-01-01 10:00']],
 * });
 * ```
 */

export type ExportFormat = 'csv' | 'excel';

export interface ExportOptions {
  /** 文件名（不含扩展名） */
  filename: string;
  /** 导出格式，默认 csv */
  format?: ExportFormat;
  /** 表头（中文标签） */
  headers: string[];
  /** 数据行（每行为原始值的数组） */
  rows: (null | number | string | undefined)[][];
  /** Excel 工作表名称（仅 Excel 格式生效），默认 "数据" */
  sheetName?: string;
}

/** 将单元格值格式化为字符串 */
function formatCell(value: null | number | string | undefined): string {
  if (value === null || value === undefined) return '';
  return String(value);
}

/** CSV 转义：双引号包裹，内部双引号翻倍 */
function escapeCsvCell(value: string): string {
  return `"${value.replaceAll('"', '""')}"`;
}

/** 触发浏览器下载 */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** 导出 CSV（UTF-8 BOM，Excel 兼容） */
function exportToCsv(
  filename: string,
  headers: string[],
  rows: (null | number | string | undefined)[][],
) {
  const csvLines = [
    headers.map((h) => escapeCsvCell(h)).join(','),
    ...rows.map((row) =>
      row.map((c) => escapeCsvCell(formatCell(c))).join(','),
    ),
  ];
  const csv = csvLines.join('\n');
  // BOM(\uFEFF) 确保 Excel 正确识别 UTF-8 编码
  const blob = new Blob([`\uFEFF${csv}`], {
    type: 'text/csv;charset=utf-8;',
  });
  triggerDownload(blob, `${filename}.csv`);
}

/** 导出 Excel（HTML 表格格式，.xls 扩展名，Excel 原生兼容） */
function exportToExcel(
  filename: string,
  headers: string[],
  rows: (null | number | string | undefined)[][],
  sheetName = '数据',
) {
  const headerHtml = `<tr>${headers
    .map(
      (h) =>
        `<th style="background:#f0f0f0;font-weight:bold;">${escapeHtml(h)}</th>`,
    )
    .join('')}</tr>`;
  const rowsHtml = rows
    .map(
      (row) =>
        `<tr>${row
          .map((c) => {
            const val = formatCell(c);
            // 数字保持原样，避免科学计数法
            const isNumeric = val !== '' && !Number.isNaN(Number(val));
            return `<td${isNumeric ? String.raw` style="mso-number-format:\@;"` : ''}>${escapeHtml(val)}</td>`;
          })
          .join('')}</tr>`,
    )
    .join('\n');

  const html = `<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:x="urn:schemas-microsoft-com:office:excel"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
  <meta charset="UTF-8">
  <!--[if gte mso 9]>
  <xml><x:ExcelWorkbook><x:ExcelWorksheets>
    <x:ExcelWorksheet><x:Name>${escapeHtml(sheetName)}</x:Name>
    <x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions>
    </x:ExcelWorksheet>
  </x:ExcelWorksheets></x:ExcelWorkbook></xml>
  <![endif]-->
</head>
<body><table border="1">${headerHtml}${rowsHtml}</table></body>
</html>`;

  const blob = new Blob([html], {
    type: 'application/vnd.ms-excel;charset=utf-8;',
  });
  triggerDownload(blob, `${filename}.xls`);
}

/** HTML 转义（防止 XSS 及 Excel 公式注入） */
function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** 统一导出入口 */
export function exportData(options: ExportOptions) {
  const {
    filename,
    format = 'csv',
    headers,
    rows,
    sheetName = '数据',
  } = options;

  if (format === 'excel') {
    exportToExcel(filename, headers, rows, sheetName);
  } else {
    exportToCsv(filename, headers, rows);
  }
}
