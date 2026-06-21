/**
 * 审计日志页面（v4.0 §6.6.3）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.6.3
 *
 * 布局结构：
 * 1. 页面标题
 * 2. FilterBar（搜索 + 操作类型筛选 + 时间范围筛选）
 * 3. DataTable（时间/用户/操作类型/资源/详情/IP地址）
 *
 * 交互：
 * - 行点击：打开 Drawer 显示完整日志详情
 * - 操作类型用不同颜色标签标识
 * - 仅查询不可物理删除（产品化原则）
 */

import { useState, useMemo } from 'react';
import { DataTable, type Column } from '../../components/DataTable';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { Drawer } from '../../components/Drawer';
import { auditLogs } from '../../mock/users';
import type { AuditLog } from '../../mock/types';

/** 操作类型颜色映射配置 */
interface ActionStyle {
  className: string;
  label: string;
  /** 自定义颜色（覆盖 className 颜色时使用） */
  color?: string;
  bgColor?: string;
  borderColor?: string;
}

/** 操作类型样式映射（按前缀分类） */
function getActionStyle(action: string): ActionStyle {
  if (action.startsWith('UPDATE')) {
    return { className: 'status-info', label: action };
  }
  if (action.startsWith('CREATE')) {
    return { className: 'status-success', label: action };
  }
  if (action.startsWith('DELETE')) {
    return { className: 'status-danger', label: action };
  }
  if (action.startsWith('RESOLVE')) {
    // 青色：使用自定义颜色（#0DCAF0）覆盖
    return {
      className: '',
      label: action,
      color: '#0A6E7E',
      bgColor: 'rgba(13, 202, 240, 0.12)',
      borderColor: 'rgba(13, 202, 240, 0.3)',
    };
  }
  if (action.startsWith('IGNORE')) {
    return { className: 'status-neutral', label: action };
  }
  return { className: 'status-neutral', label: action };
}

/** 操作类型筛选选项 */
const ACTION_OPTIONS = [
  { label: 'UPDATE_CONFIG', value: 'UPDATE_CONFIG' },
  { label: 'UPDATE_MAPPING', value: 'UPDATE_MAPPING' },
  { label: 'UPDATE_USER', value: 'UPDATE_USER' },
  { label: 'CREATE_LOOP', value: 'CREATE_LOOP' },
  { label: 'CREATE_REPORT', value: 'CREATE_REPORT' },
  { label: 'RESOLVE_TRACKER', value: 'RESOLVE_TRACKER' },
  { label: 'IGNORE_TRACKER', value: 'IGNORE_TRACKER' },
];

/** 时间范围筛选选项 */
const TIME_RANGE_OPTIONS = [
  { label: '今天', value: 'today' },
  { label: '最近 7 天', value: '7d' },
  { label: '最近 30 天', value: '30d' },
];

/** 判断日志是否在时间范围内 */
function isInTimeRange(logTime: string, range: string): boolean {
  if (!range) return true;
  const logDate = new Date(logTime.replace(' ', 'T'));
  const now = new Date('2026-06-21T23:59:59');
  const days = range === 'today' ? 0 : range === '7d' ? 7 : 30;
  const start = new Date(now);
  start.setDate(start.getDate() - days);
  start.setHours(0, 0, 0, 0);
  return logDate >= start && logDate <= now;
}

export default function AuditLogPage() {
  // 筛选状态
  const [search, setSearch] = useState('');
  const [actionFilter, setActionFilter] = useState('');
  const [timeRange, setTimeRange] = useState('');

  // Drawer 详情状态
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);

  /** 筛选后的日志列表 */
  const filteredLogs = useMemo(() => {
    return auditLogs.filter((log) => {
      const matchSearch =
        !search ||
        log.username.toLowerCase().includes(search.toLowerCase()) ||
        log.resource.toLowerCase().includes(search.toLowerCase()) ||
        log.detail.toLowerCase().includes(search.toLowerCase());
      const matchAction = !actionFilter || log.action === actionFilter;
      const matchTime = isInTimeRange(log.createdAt, timeRange);
      return matchSearch && matchAction && matchTime;
    });
  }, [search, actionFilter, timeRange]);

  /** 筛选项 */
  const filters: FilterItem[] = [
    {
      key: 'action',
      label: '操作类型',
      type: 'select',
      options: ACTION_OPTIONS,
      value: actionFilter,
      onChange: setActionFilter,
    },
    {
      key: 'timeRange',
      label: '时间范围',
      type: 'select',
      options: TIME_RANGE_OPTIONS,
      value: timeRange,
      onChange: setTimeRange,
    },
  ];

  /** 渲染操作类型标签 */
  const renderActionBadge = (action: string) => {
    const style = getActionStyle(action);
    if (style.color) {
      return (
        <span
          className="badge badge-sm mono"
          style={{
            color: style.color,
            background: style.bgColor,
            borderColor: style.borderColor,
          }}
        >
          {action}
        </span>
      );
    }
    return (
      <span className={`badge ${style.className} badge-sm mono`}>{action}</span>
    );
  };

  /** 表格列定义 */
  const columns: Column<AuditLog>[] = [
    {
      key: 'createdAt',
      header: '时间',
      sortable: true,
      width: '160px',
      render: (row) => <span className="mono">{row.createdAt}</span>,
    },
    {
      key: 'username',
      header: '用户',
      sortable: true,
      width: '120px',
      render: (row) => (
        <span>
          {row.username}
          <span className="text-muted mono" style={{ fontSize: 'var(--text-small)', marginLeft: 'var(--space-1)' }}>
            ({row.userId})
          </span>
        </span>
      ),
    },
    {
      key: 'action',
      header: '操作类型',
      sortable: true,
      width: '160px',
      render: (row) => renderActionBadge(row.action),
    },
    {
      key: 'resource',
      header: '资源',
      sortable: true,
      width: '200px',
      render: (row) => <span className="mono">{row.resource}</span>,
    },
    {
      key: 'detail',
      header: '详情',
      render: (row) => (
        <span
          style={{
            display: 'inline-block',
            maxWidth: '320px',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
          title={row.detail}
        >
          {row.detail}
        </span>
      ),
    },
    {
      key: 'ipAddress',
      header: 'IP 地址',
      width: '140px',
      render: (row) => <span className="mono">{row.ipAddress}</span>,
    },
  ];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>审计日志</h1>
          <p className="page-subtitle">
            系统操作审计记录 · 共 {auditLogs.length} 条 · 仅查询不可物理删除
          </p>
        </div>
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索用户、资源或详情..."
        filters={filters}
        showClearAll
        onClearAll={() => {
          setSearch('');
          setActionFilter('');
          setTimeRange('');
        }}
      />

      {/* 日志列表 */}
      <DataTable
        columns={columns}
        data={filteredLogs}
        rowKey={(row) => row.logId}
        onRowClick={(row) => setSelectedLog(row)}
        emptyText="无符合条件的审计日志"
      />

      {/* 日志详情 Drawer */}
      <Drawer
        open={!!selectedLog}
        title="审计日志详情"
        onClose={() => setSelectedLog(null)}
      >
        {selectedLog && (
          <div>
            <div className="form-section">
              <div className="form-section-header">
                <h3>基本信息</h3>
              </div>
              <div className="form-section-body">
                <div className="form-row">
                  <label>日志 ID</label>
                  <span className="mono">{selectedLog.logId}</span>
                </div>
                <div className="form-row">
                  <label>时间</label>
                  <span className="mono">{selectedLog.createdAt}</span>
                </div>
                <div className="form-row">
                  <label>用户</label>
                  <span>
                    {selectedLog.username}
                    <span className="text-muted mono" style={{ fontSize: 'var(--text-small)', marginLeft: 'var(--space-1)' }}>
                      ({selectedLog.userId})
                    </span>
                  </span>
                </div>
                <div className="form-row">
                  <label>IP 地址</label>
                  <span className="mono">{selectedLog.ipAddress}</span>
                </div>
              </div>
            </div>

            <div className="form-section">
              <div className="form-section-header">
                <h3>操作信息</h3>
              </div>
              <div className="form-section-body">
                <div className="form-row">
                  <label>操作类型</label>
                  {renderActionBadge(selectedLog.action)}
                </div>
                <div className="form-row">
                  <label>资源</label>
                  <span className="mono">{selectedLog.resource}</span>
                </div>
                <div className="form-row" style={{ gridTemplateColumns: '120px 1fr', alignItems: 'flex-start' }}>
                  <label>详情</label>
                  <span style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                    {selectedLog.detail}
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
