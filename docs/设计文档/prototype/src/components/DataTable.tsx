/**
 * 高密度数据表格（v4.0 通用组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.4 + §8.1
 *
 * 特性：
 * - 列定义（key/header/render/sortable/width/align）
 * - 点击表头排序（单字段）
 * - 行点击回调（进入详情页）
 * - 固定表头
 * - 高密度行高 32px（§7.4）
 * - 空状态/Loading 状态（§8.1）
 * - 等宽列：位号/评分/数值/时间戳自动应用 .mono
 */

import { useState, useMemo } from 'react';
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';

/** 列定义 */
export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
  width?: string;
  align?: 'left' | 'center' | 'right';
  /** 排序值提取器（默认用 row[key]） */
  sortValue?: (row: T) => string | number;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  /** 行唯一键 */
  rowKey: (row: T) => string;
  /** 行点击回调 */
  onRowClick?: (row: T) => void;
  /** 空状态文案 */
  emptyText?: string;
  /** Loading 状态 */
  loading?: boolean;
  /** 初始排序字段 */
  initialSortKey?: string;
  initialSortDir?: 'asc' | 'desc';
}

export function DataTable<T>({
  columns,
  data,
  rowKey,
  onRowClick,
  emptyText = '暂无数据',
  loading = false,
  initialSortKey,
  initialSortDir = 'asc',
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | undefined>(initialSortKey);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(initialSortDir);

  const handleSort = (col: Column<T>) => {
    if (!col.sortable) return;
    if (sortKey === col.key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(col.key);
      setSortDir('asc');
    }
  };

  const sortedData = useMemo(() => {
    if (!sortKey) return data;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return data;
    const getValue = col.sortValue ?? ((row: T) => {
      const v = (row as Record<string, unknown>)[sortKey];
      return v as string | number;
    });
    return [...data].sort((a, b) => {
      const va = getValue(a);
      const vb = getValue(b);
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      if (typeof va === 'number' && typeof vb === 'number') {
        return sortDir === 'asc' ? va - vb : vb - va;
      }
      const sa = String(va);
      const sb = String(vb);
      return sortDir === 'asc' ? sa.localeCompare(sb) : sb.localeCompare(sa);
    });
  }, [data, sortKey, sortDir, columns]);

  if (loading) {
    return (
      <div className="data-table-loading">
        <div className="data-table-spinner" />
        <span>加载中...</span>
      </div>
    );
  }

  if (sortedData.length === 0) {
    return <div className="data-table-empty">{emptyText}</div>;
  }

  return (
    <div className="data-table-wrapper">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((col) => {
              const isSorted = sortKey === col.key;
              const SortIcon = isSorted
                ? sortDir === 'asc'
                  ? ChevronUp
                  : ChevronDown
                : ChevronsUpDown;
              return (
                <th
                  key={col.key}
                  style={{ width: col.width, textAlign: col.align ?? 'left' }}
                  className={col.sortable ? 'sortable' : ''}
                  onClick={() => handleSort(col)}
                >
                  <span>{col.header}</span>
                  {col.sortable && <SortIcon size={12} className="sort-icon" />}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedData.map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? 'clickable' : ''}
              onClick={() => onRowClick?.(row)}
            >
              {columns.map((col) => (
                <td key={col.key} style={{ textAlign: col.align ?? 'left' }}>
                  {col.render
                    ? col.render(row)
                    : String((row as Record<string, unknown>)[col.key] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
