/**
 * 自动报表页面（v4.0 §6.6.4）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.6.4
 *
 * 布局结构：
 * 1. 页面标题
 * 2. FilterBar（搜索 + 报表类型筛选 + 启用状态筛选 + 新增报表按钮）
 * 3. DataTable（报表名称/报表类型/调度计划/收件人/启用状态/最后生成时间/操作）
 *
 * 交互：
 * - 新增/编辑报表：Drawer 表单
 * - 启停报表：ConfigConfirmDialog 确认
 * - 操作反馈：useToast
 * - 报表类型：班报/日报/周报/月报，用不同颜色标签
 */

import { useState, useMemo } from 'react';
import { Plus, Pencil, Power, Mail } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { Drawer } from '../../components/Drawer';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { useToast } from '../../components/Toast';
import { reportConfigs as initialReports } from '../../mock/users';
import type { ReportConfig } from '../../mock/types';

/** 报表表单状态 */
interface ReportFormState {
  reportName: string;
  reportType: ReportConfig['reportType'];
  schedule: string;
  recipients: string;
  enabled: boolean;
}

/** 空表单初始值 */
const EMPTY_FORM: ReportFormState = {
  reportName: '',
  reportType: '日报',
  schedule: '',
  recipients: '',
  enabled: true,
};

/** 报表类型样式映射 */
const REPORT_TYPE_STYLE: Record<ReportConfig['reportType'], string> = {
  班报: 'status-info',
  日报: 'status-success',
  周报: 'status-warning',
  月报: 'status-neutral',
};

/** 报表类型筛选选项 */
const REPORT_TYPE_OPTIONS = [
  { label: '班报', value: '班报' },
  { label: '日报', value: '日报' },
  { label: '周报', value: '周报' },
  { label: '月报', value: '月报' },
];

export default function ReportsPage() {
  const toast = useToast();
  const [reportList, setReportList] = useState<ReportConfig[]>(initialReports);

  // 筛选状态
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [enabledFilter, setEnabledFilter] = useState('');

  // Drawer 表单状态
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingReport, setEditingReport] = useState<ReportConfig | null>(null);
  const [form, setForm] = useState<ReportFormState>(EMPTY_FORM);

  // 确认弹窗状态
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmReport, setConfirmReport] = useState<ReportConfig | null>(null);
  const [confirmChanges, setConfirmChanges] = useState<ChangeEntry[]>([]);

  /** 筛选后的报表列表 */
  const filteredReports = useMemo(() => {
    return reportList.filter((r) => {
      const matchSearch =
        !search ||
        r.reportName.toLowerCase().includes(search.toLowerCase()) ||
        r.schedule.toLowerCase().includes(search.toLowerCase());
      const matchType = !typeFilter || r.reportType === typeFilter;
      const matchEnabled =
        !enabledFilter ||
        (enabledFilter === 'true' && r.enabled) ||
        (enabledFilter === 'false' && !r.enabled);
      return matchSearch && matchType && matchEnabled;
    });
  }, [reportList, search, typeFilter, enabledFilter]);

  /** 筛选项 */
  const filters: FilterItem[] = [
    {
      key: 'type',
      label: '报表类型',
      type: 'select',
      options: REPORT_TYPE_OPTIONS,
      value: typeFilter,
      onChange: setTypeFilter,
    },
    {
      key: 'enabled',
      label: '状态',
      type: 'select',
      options: [
        { label: '启用', value: 'true' },
        { label: '停用', value: 'false' },
      ],
      value: enabledFilter,
      onChange: setEnabledFilter,
    },
  ];

  /** 打开新增报表 Drawer */
  const handleOpenAdd = () => {
    setEditingReport(null);
    setForm(EMPTY_FORM);
    setDrawerOpen(true);
  };

  /** 打开编辑报表 Drawer */
  const handleOpenEdit = (report: ReportConfig) => {
    setEditingReport(report);
    setForm({
      reportName: report.reportName,
      reportType: report.reportType,
      schedule: report.schedule,
      recipients: report.recipients.join('\n'),
      enabled: report.enabled,
    });
    setDrawerOpen(true);
  };

  /** 打开启停确认弹窗 */
  const handleToggleEnabled = (report: ReportConfig) => {
    setConfirmReport(report);
    setConfirmChanges([
      {
        field: 'enabled',
        oldValue: report.enabled ? '启用' : '停用',
        newValue: report.enabled ? '停用' : '启用',
      },
    ]);
    setConfirmOpen(true);
  };

  /** 确认启停 */
  const handleConfirm = (comment: string) => {
    if (!confirmReport) return;
    setReportList((prev) =>
      prev.map((r) =>
        r.reportId === confirmReport.reportId
          ? { ...r, enabled: !r.enabled }
          : r,
      ),
    );
    toast.success(`报表 ${confirmReport.reportName} 已${confirmReport.enabled ? '停用' : '启用'}（变更说明：${comment}）`);
    setConfirmOpen(false);
    setConfirmReport(null);
  };

  /** 提交表单 */
  const handleSubmitForm = () => {
    if (!form.reportName.trim()) {
      toast.warning('报表名称不能为空');
      return;
    }
    if (!form.schedule.trim()) {
      toast.warning('调度计划不能为空');
      return;
    }
    const recipients = form.recipients
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    if (recipients.length === 0) {
      toast.warning('至少需要一个收件人');
      return;
    }

    if (editingReport) {
      setReportList((prev) =>
        prev.map((r) =>
          r.reportId === editingReport.reportId
            ? {
                ...r,
                reportName: form.reportName,
                reportType: form.reportType,
                schedule: form.schedule,
                recipients,
                enabled: form.enabled,
              }
            : r,
        ),
      );
      toast.success(`报表 ${form.reportName} 已更新`);
    } else {
      const newReport: ReportConfig = {
        reportId: `R${String(reportList.length + 1).padStart(3, '0')}`,
        reportName: form.reportName,
        reportType: form.reportType,
        schedule: form.schedule,
        recipients,
        enabled: form.enabled,
        lastGeneratedAt: null,
      };
      setReportList((prev) => [...prev, newReport]);
      toast.success(`报表 ${form.reportName} 已创建`);
    }
    setDrawerOpen(false);
    setEditingReport(null);
    setForm(EMPTY_FORM);
  };

  /** 表格列定义 */
  const columns: Column<ReportConfig>[] = [
    {
      key: 'reportName',
      header: '报表名称',
      sortable: true,
      width: '200px',
    },
    {
      key: 'reportType',
      header: '报表类型',
      sortable: true,
      width: '100px',
      render: (row) => (
        <span className={`badge ${REPORT_TYPE_STYLE[row.reportType]} badge-sm`}>
          {row.reportType}
        </span>
      ),
    },
    {
      key: 'schedule',
      header: '调度计划',
      width: '220px',
      render: (row) => <span className="mono">{row.schedule}</span>,
    },
    {
      key: 'recipients',
      header: '收件人',
      render: (row) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)' }}>
          <Mail size={12} className="text-muted" />
          <span
            style={{
              display: 'inline-block',
              maxWidth: '240px',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
            title={row.recipients.join(', ')}
            className="mono"
          >
            {row.recipients.join(', ')}
          </span>
          <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
            ({row.recipients.length})
          </span>
        </div>
      ),
    },
    {
      key: 'enabled',
      header: '启用状态',
      sortable: true,
      width: '90px',
      render: (row) =>
        row.enabled ? (
          <span className="badge status-success badge-sm">启用</span>
        ) : (
          <span className="badge status-neutral badge-sm">停用</span>
        ),
    },
    {
      key: 'lastGeneratedAt',
      header: '最后生成时间',
      sortable: true,
      width: '160px',
      render: (row) => (
        <span className="mono">{row.lastGeneratedAt ?? '—'}</span>
      ),
    },
    {
      key: 'actions',
      header: '操作',
      width: '140px',
      render: (row) => (
        <div style={{ display: 'flex', gap: 'var(--space-1)' }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: 'var(--text-small)' }}
            onClick={(e) => {
              e.stopPropagation();
              handleOpenEdit(row);
            }}
          >
            <Pencil size={12} />
            编辑
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: 'var(--text-small)' }}
            onClick={(e) => {
              e.stopPropagation();
              handleToggleEnabled(row);
            }}
          >
            <Power size={12} />
            {row.enabled ? '停用' : '启用'}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>自动报表管理</h1>
          <p className="page-subtitle">
            管理班报/日报/周报/月报配置 · 共 {reportList.length} 个报表 ·
            启用 {reportList.filter((r) => r.enabled).length} 个
          </p>
        </div>
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索报表名称或调度计划..."
        filters={filters}
        showClearAll
        onClearAll={() => {
          setSearch('');
          setTypeFilter('');
          setEnabledFilter('');
        }}
        actions={
          <button type="button" className="btn btn-primary" onClick={handleOpenAdd}>
            <Plus size={14} />
            新增报表
          </button>
        }
      />

      {/* 报表列表 */}
      <DataTable
        columns={columns}
        data={filteredReports}
        rowKey={(row) => row.reportId}
        emptyText="无符合条件的报表配置"
      />

      {/* 新增/编辑报表 Drawer */}
      <Drawer
        open={drawerOpen}
        title={editingReport ? `编辑报表：${editingReport.reportName}` : '新增报表'}
        onClose={() => {
          setDrawerOpen(false);
          setEditingReport(null);
          setForm(EMPTY_FORM);
        }}
        footer={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setDrawerOpen(false);
                setEditingReport(null);
                setForm(EMPTY_FORM);
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSubmitForm}
            >
              {editingReport ? '保存' : '创建'}
            </button>
          </>
        }
      >
        <div className="form-section">
          <div className="form-section-header">
            <h3>报表配置</h3>
          </div>
          <div className="form-section-body">
            <div className="form-row">
              <label>报表名称 *</label>
              <input
                type="text"
                value={form.reportName}
                onChange={(e) => setForm({ ...form, reportName: e.target.value })}
                placeholder="如：加氢联合车间班报"
              />
            </div>
            <div className="form-row">
              <label>报表类型 *</label>
              <select
                value={form.reportType}
                onChange={(e) =>
                  setForm({ ...form, reportType: e.target.value as ReportConfig['reportType'] })
                }
              >
                {REPORT_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label>调度计划 *</label>
              <input
                type="text"
                value={form.schedule}
                onChange={(e) => setForm({ ...form, schedule: e.target.value })}
                placeholder="如：每日 07:00"
              />
            </div>
            <div className="form-row" style={{ gridTemplateColumns: '120px 1fr', alignItems: 'flex-start' }}>
              <label>收件人列表 *</label>
              <div>
                <textarea
                  value={form.recipients}
                  onChange={(e) => setForm({ ...form, recipients: e.target.value })}
                  placeholder="每行一个邮箱地址"
                  rows={4}
                  style={{ width: '100%', fontFamily: 'var(--font-mono)' }}
                />
                <span className="hint">每行一个邮箱地址，至少一个</span>
              </div>
            </div>
            <div className="form-row">
              <label>启用状态</label>
              <label style={{ textAlign: 'left', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  style={{ width: 'auto' }}
                />
                <span>{form.enabled ? '启用' : '停用'}</span>
              </label>
            </div>
          </div>
        </div>
      </Drawer>

      {/* 启停确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName={`报表 ${confirmReport?.reportName ?? ''}`}
        changes={confirmChanges}
        onCancel={() => {
          setConfirmOpen(false);
          setConfirmReport(null);
        }}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
