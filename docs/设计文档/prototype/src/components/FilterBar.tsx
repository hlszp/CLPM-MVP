/**
 * 筛选栏（v4.0 通用组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.5
 *
 * 结构：搜索框 + 筛选项（下拉/日期范围）+ 操作按钮区
 * 所有列表页统一使用此组件作为顶部筛选区。
 */

import type { ReactNode } from 'react';
import { Search, X } from 'lucide-react';

/** 筛选项定义 */
export interface FilterItem {
  key: string;
  label: string;
  type: 'select' | 'text';
  options?: Array<{ label: string; value: string }>;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

interface FilterBarProps {
  /** 搜索关键词 */
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder?: string;
  /** 筛选项列表 */
  filters?: FilterItem[];
  /** 右侧操作按钮区 */
  actions?: ReactNode;
  /** 是否显示清除全部 */
  showClearAll?: boolean;
  onClearAll?: () => void;
}

export function FilterBar({
  searchValue,
  onSearchChange,
  searchPlaceholder = '搜索...',
  filters = [],
  actions,
  showClearAll = false,
  onClearAll,
}: FilterBarProps) {
  const hasActiveFilters = searchValue || filters.some((f) => f.value);

  return (
    <div className="filter-bar">
      <div className="filter-bar-left">
        <div className="filter-search">
          <Search size={14} />
          <input
            type="text"
            placeholder={searchPlaceholder}
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
        {filters.map((f) => (
          <div key={f.key} className="filter-item">
            <label>{f.label}</label>
            {f.type === 'select' ? (
              <select value={f.value} onChange={(e) => f.onChange(e.target.value)}>
                <option value="">全部</option>
                {f.options?.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                placeholder={f.placeholder ?? ''}
                value={f.value}
                onChange={(e) => f.onChange(e.target.value)}
              />
            )}
          </div>
        ))}
        {hasActiveFilters && showClearAll && (
          <button type="button" className="filter-clear" onClick={onClearAll}>
            <X size={12} />
            清除
          </button>
        )}
      </div>
      {actions && <div className="filter-bar-actions">{actions}</div>}
    </div>
  );
}
