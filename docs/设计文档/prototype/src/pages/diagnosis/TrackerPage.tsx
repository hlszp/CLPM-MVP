/**
 * 异常跟踪页（v4.0 §6.4.4）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.4.4
 *
 * 布局结构：
 * 1. 顶部统计卡片：待处理 / 进行中 / 已解决 / 已忽略（4 个卡片）
 * 2. FilterBar：搜索 + 处理状态下拉 + 预诊标签下拉
 * 3. DataTable：Tracker ID/回路名/节点/预诊标签/处理状态/负责人/创建时间/更新时间/操作
 *
 * 行点击打开 Drawer 显示 Tracker 详情：
 * - 基本信息 + 处理说明 + 状态流转按钮（开始处理/标记已解决/忽略）
 * - 标记已解决时需填写 A/B 对比基准时间范围
 */

import { useState, useMemo } from 'react';
import { Clock, Activity, CheckCircle2, XCircle, ArrowRight } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { Drawer } from '../../components/Drawer';
import { ActionStatusBadge, DiagnosisLabelBadge, type ActionStatus } from '../../components/StatusBadge';
import { useToast } from '../../components/Toast';
import {
  actionTrackers,
} from '../../mock/tracker';
import type { ActionTracker } from '../../mock/types';
import { getDiagnosisStatsByLabel } from '../../mock/diagnosis';

/** 顶部统计卡片 */
function StatCard({
  label,
  value,
  icon: Icon,
  color,
  active,
  onClick,
}: {
  label: string;
  value: number;
  icon: typeof Clock;
  color: string;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <div
      className="kpi-card"
      style={{ cursor: onClick ? 'pointer' : 'default', borderColor: active ? color : undefined }}
      onClick={onClick}
    >
      <div className="kpi-card-header">
        <span className="kpi-card-label">{label}</span>
        <Icon size={16} className="kpi-card-icon" style={{ color }} />
      </div>
      <div className="kpi-card-value" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

export default function TrackerPage() {
  const toast = useToast();

  // 筛选状态
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [labelFilter, setLabelFilter] = useState('');

  // Drawer 状态
  const [selectedTracker, setSelectedTracker] = useState<ActionTracker | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 状态流转表单
  const [comment, setComment] = useState('');
  const [baselineStart, setBaselineStart] = useState('');
  const [baselineEnd, setBaselineEnd] = useState('');
  const [resolveMode, setResolveMode] = useState(false);

  // 本地 Tracker 列表（支持状态变更后即时更新）
  const [trackers, setTrackers] = useState<ActionTracker[]>(actionTrackers);

  // 统计数据
  const stats = useMemo(() => {
    const pending = trackers.filter((t) => t.actionStatus === 'PENDING').length;
    const inProgress = trackers.filter((t) => t.actionStatus === 'IN_PROGRESS').length;
    const resolved = trackers.filter((t) => t.actionStatus === 'RESOLVED').length;
    const ignored = trackers.filter((t) => t.actionStatus === 'IGNORED').length;
    return { pending, inProgress, resolved, ignored };
  }, [trackers]);

  // 预诊标签选项
  const labelOptions = useMemo(() => {
    return getDiagnosisStatsByLabel().map((s) => ({ label: s.label, value: s.label }));
  }, []);

  // 筛选项
  const filters: FilterItem[] = [
    {
      key: 'status',
      label: '处理状态',
      type: 'select',
      options: [
        { label: '待处理', value: 'PENDING' },
        { label: '处理中', value: 'IN_PROGRESS' },
        { label: '已解决', value: 'RESOLVED' },
        { label: '已忽略', value: 'IGNORED' },
      ],
      value: statusFilter,
      onChange: setStatusFilter,
    },
    {
      key: 'label',
      label: '预诊标签',
      type: 'select',
      options: labelOptions,
      value: labelFilter,
      onChange: setLabelFilter,
    },
  ];

  // 过滤后的数据
  const filteredData = useMemo(() => {
    return trackers.filter((t) => {
      if (search) {
        const kw = search.toLowerCase();
        if (
          !t.loopName.toLowerCase().includes(kw) &&
          !t.nodeName.toLowerCase().includes(kw) &&
          !t.trackerId.toLowerCase().includes(kw) &&
          !t.assignee.toLowerCase().includes(kw)
        ) {
          return false;
        }
      }
      if (statusFilter && t.actionStatus !== statusFilter) return false;
      if (labelFilter && t.label !== labelFilter) return false;
      return true;
    });
  }, [trackers, search, statusFilter, labelFilter]);

  // 清除全部筛选
  const handleClearAll = () => {
    setSearch('');
    setStatusFilter('');
    setLabelFilter('');
  };

  // 打开 Drawer
  const handleRowClick = (tracker: ActionTracker) => {
    setSelectedTracker(tracker);
    setComment(tracker.comment);
    setBaselineStart(tracker.baselineStart ?? '');
    setBaselineEnd(tracker.baselineEnd ?? '');
    setResolveMode(false);
    setDrawerOpen(true);
  };

  // 状态流转：开始处理
  const handleStartProcess = () => {
    if (!selectedTracker) return;
    if (!comment.trim()) {
      toast.warning('请填写处理说明后再开始处理');
      return;
    }
    setTrackers((prev) =>
      prev.map((t) =>
        t.trackerId === selectedTracker.trackerId
          ? { ...t, actionStatus: 'IN_PROGRESS', comment: comment.trim(), updatedAt: '2026-06-21 10:30:00' }
          : t,
      ),
    );
    toast.success(`Tracker ${selectedTracker.trackerId} 已标记为处理中`);
    setSelectedTracker(null);
    setDrawerOpen(false);
  };

  // 进入"标记已解决"模式
  const handleEnterResolveMode = () => {
    setResolveMode(true);
  };

  // 确认标记已解决
  const handleConfirmResolve = () => {
    if (!selectedTracker) return;
    if (!comment.trim()) {
      toast.warning('请填写处理说明');
      return;
    }
    if (!baselineStart || !baselineEnd) {
      toast.warning('请填写 A/B 对比基准时间范围');
      return;
    }
    setTrackers((prev) =>
      prev.map((t) =>
        t.trackerId === selectedTracker.trackerId
          ? {
              ...t,
              actionStatus: 'RESOLVED',
              comment: comment.trim(),
              baselineStart,
              baselineEnd,
              updatedAt: '2026-06-21 10:30:00',
            }
          : t,
      ),
    );
    toast.success(`Tracker ${selectedTracker.trackerId} 已标记为已解决，可前往 A/B 对比查看效果`);
    setSelectedTracker(null);
    setDrawerOpen(false);
    setResolveMode(false);
  };

  // 忽略
  const handleIgnore = () => {
    if (!selectedTracker) return;
    if (!comment.trim()) {
      toast.warning('请填写忽略原因');
      return;
    }
    setTrackers((prev) =>
      prev.map((t) =>
        t.trackerId === selectedTracker.trackerId
          ? { ...t, actionStatus: 'IGNORED', comment: comment.trim(), updatedAt: '2026-06-21 10:30:00' }
          : t,
      ),
    );
    toast.success(`Tracker ${selectedTracker.trackerId} 已忽略`);
    setSelectedTracker(null);
    setDrawerOpen(false);
  };

  // 列定义
  const columns: Column<ActionTracker>[] = [
    {
      key: 'trackerId',
      header: 'Tracker ID',
      sortable: true,
      width: '100px',
      render: (row) => <span className="mono" style={{ fontWeight: 500 }}>{row.trackerId}</span>,
    },
    {
      key: 'loopName',
      header: '回路名',
      sortable: true,
      render: (row) => (
        <div>
          <div style={{ fontWeight: 500 }}>{row.loopName}</div>
          <div className="mono" style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)' }}>
            {row.loopId}
          </div>
        </div>
      ),
    },
    {
      key: 'nodeName',
      header: '节点',
      sortable: true,
      width: '110px',
    },
    {
      key: 'label',
      header: '预诊标签',
      width: '120px',
      render: (row) => <DiagnosisLabelBadge label={row.label} />,
    },
    {
      key: 'actionStatus',
      header: '处理状态',
      width: '110px',
      sortable: true,
      sortValue: (row) => row.actionStatus,
      render: (row) => <ActionStatusBadge status={row.actionStatus} />,
    },
    {
      key: 'assignee',
      header: '负责人',
      width: '120px',
    },
    {
      key: 'createdAt',
      header: '创建时间',
      sortable: true,
      width: '150px',
      render: (row) => <span className="mono" style={{ fontSize: 'var(--text-small)' }}>{row.createdAt}</span>,
    },
    {
      key: 'updatedAt',
      header: '更新时间',
      sortable: true,
      width: '150px',
      render: (row) => <span className="mono" style={{ fontSize: 'var(--text-small)' }}>{row.updatedAt}</span>,
    },
    {
      key: 'action',
      header: '操作',
      width: '80px',
      align: 'center',
      render: () => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, color: 'var(--accent-blue)', fontSize: 'var(--text-small)' }}>
          详情 <ArrowRight size={12} />
        </span>
      ),
    },
  ];

  // Drawer 底部按钮
  const drawerFooter = selectedTracker && (
    <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
      {resolveMode ? (
        <>
          <button type="button" className="btn btn-secondary" onClick={() => setResolveMode(false)}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={handleConfirmResolve}>
            <CheckCircle2 size={14} />
            确认已解决
          </button>
        </>
      ) : (
        <>
          {selectedTracker.actionStatus === 'PENDING' && (
            <button type="button" className="btn btn-primary" onClick={handleStartProcess}>
              <Activity size={14} />
              开始处理
            </button>
          )}
          {selectedTracker.actionStatus === 'IN_PROGRESS' && (
            <button type="button" className="btn btn-primary" onClick={handleEnterResolveMode}>
              <CheckCircle2 size={14} />
              标记已解决
            </button>
          )}
          {(selectedTracker.actionStatus === 'PENDING' || selectedTracker.actionStatus === 'IN_PROGRESS') && (
            <button type="button" className="btn btn-secondary" onClick={handleIgnore}>
              <XCircle size={14} />
              忽略
            </button>
          )}
          {selectedTracker.actionStatus === 'RESOLVED' && (
            <span className="badge status-success badge-md">
              <CheckCircle2 size={12} />
              已解决
            </span>
          )}
          {selectedTracker.actionStatus === 'IGNORED' && (
            <span className="badge status-neutral badge-md">
              <XCircle size={12} />
              已忽略
            </span>
          )}
        </>
      )}
    </div>
  );

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>异常跟踪</h1>
          <p className="page-subtitle">
            共 {trackers.length} 个 Tracker · 状态流转：待处理 → 处理中 → 已解决/已忽略
          </p>
        </div>
      </div>

      {/* 顶部统计卡片 */}
      <div className="kpi-grid">
        <StatCard
          label="待处理"
          value={stats.pending}
          icon={Clock}
          color="var(--status-danger)"
          active={statusFilter === 'PENDING'}
          onClick={() => setStatusFilter(statusFilter === 'PENDING' ? '' : 'PENDING')}
        />
        <StatCard
          label="进行中"
          value={stats.inProgress}
          icon={Activity}
          color="var(--accent-blue)"
          active={statusFilter === 'IN_PROGRESS'}
          onClick={() => setStatusFilter(statusFilter === 'IN_PROGRESS' ? '' : 'IN_PROGRESS')}
        />
        <StatCard
          label="已解决"
          value={stats.resolved}
          icon={CheckCircle2}
          color="var(--status-ok)"
          active={statusFilter === 'RESOLVED'}
          onClick={() => setStatusFilter(statusFilter === 'RESOLVED' ? '' : 'RESOLVED')}
        />
        <StatCard
          label="已忽略"
          value={stats.ignored}
          icon={XCircle}
          color="var(--text-muted)"
          active={statusFilter === 'IGNORED'}
          onClick={() => setStatusFilter(statusFilter === 'IGNORED' ? '' : 'IGNORED')}
        />
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索 Tracker ID / 回路名 / 负责人"
        filters={filters}
        showClearAll={!!(search || statusFilter || labelFilter)}
        onClearAll={handleClearAll}
      />

      {/* 数据表格 */}
      <DataTable
        columns={columns}
        data={filteredData}
        rowKey={(row) => row.trackerId}
        onRowClick={handleRowClick}
        emptyText="无匹配的 Tracker"
        initialSortKey="updatedAt"
        initialSortDir="desc"
      />

      {/* Tracker 详情 Drawer */}
      <Drawer
        open={drawerOpen}
        title={selectedTracker ? `Tracker ${selectedTracker.trackerId}` : ''}
        onClose={() => {
          setDrawerOpen(false);
          setSelectedTracker(null);
          setResolveMode(false);
        }}
        footer={drawerFooter}
        width="520px"
      >
        {selectedTracker && (
          <div>
            {/* 基本信息 */}
            <div className="form-section" style={{ marginBottom: 'var(--space-4)' }}>
              <div className="form-section-header">
                <h3>基本信息</h3>
              </div>
              <div className="form-section-body">
                <div className="form-row">
                  <label>回路名</label>
                  <div>{selectedTracker.loopName}</div>
                </div>
                <div className="form-row">
                  <label>回路位号</label>
                  <div className="mono">{selectedTracker.loopId}</div>
                </div>
                <div className="form-row">
                  <label>所属节点</label>
                  <div>{selectedTracker.nodeName}</div>
                </div>
                <div className="form-row">
                  <label>预诊标签</label>
                  <div><DiagnosisLabelBadge label={selectedTracker.label} /></div>
                </div>
                <div className="form-row">
                  <label>处理状态</label>
                  <div><ActionStatusBadge status={selectedTracker.actionStatus} size="md" /></div>
                </div>
                <div className="form-row">
                  <label>负责人</label>
                  <div>{selectedTracker.assignee}</div>
                </div>
                <div className="form-row">
                  <label>创建时间</label>
                  <div className="mono">{selectedTracker.createdAt}</div>
                </div>
                <div className="form-row">
                  <label>更新时间</label>
                  <div className="mono">{selectedTracker.updatedAt}</div>
                </div>
              </div>
            </div>

            {/* 处理说明 */}
            <div className="form-section" style={{ marginBottom: 'var(--space-4)' }}>
              <div className="form-section-header">
                <h3>处理说明</h3>
              </div>
              <div className="form-section-body">
                <div className="form-row" style={{ gridTemplateColumns: '1fr' }}>
                  <textarea
                    placeholder="请填写处理说明 / 调整内容 / 忽略原因（必填）"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    rows={4}
                    style={{
                      width: '100%',
                      padding: 'var(--space-2) var(--space-3)',
                      fontSize: 'var(--text-body)',
                      border: '1px solid var(--border-default)',
                      borderRadius: 'var(--radius-sm)',
                      outline: 'none',
                      resize: 'vertical',
                      fontFamily: 'var(--font-sans)',
                    }}
                  />
                </div>
              </div>
            </div>

            {/* A/B 对比基准时间（标记已解决模式时显示） */}
            {resolveMode && (
              <div className="form-section" style={{ marginBottom: 'var(--space-4)', borderColor: 'var(--accent-blue)' }}>
                <div className="form-section-header" style={{ background: 'var(--accent-blue-bg)' }}>
                  <h3 style={{ color: 'var(--accent-blue)' }}>A/B 对比基准时间范围</h3>
                </div>
                <div className="form-section-body">
                  <div className="form-row">
                    <label>基准开始时间</label>
                    <input
                      type="text"
                      placeholder="如 2026-06-18 00:00:00"
                      value={baselineStart}
                      onChange={(e) => setBaselineStart(e.target.value)}
                    />
                  </div>
                  <div className="form-row">
                    <label>基准结束时间</label>
                    <input
                      type="text"
                      placeholder="如 2026-06-19 00:00:00"
                      value={baselineEnd}
                      onChange={(e) => setBaselineEnd(e.target.value)}
                    />
                  </div>
                  <div className="form-row">
                    <span className="hint" style={{ gridColumn: '1 / -1', textAlign: 'left' }}>
                      基准时间范围用于 A/B 对比，将对比调整前与调整后的回路性能指标
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* 已解决时的基准信息 */}
            {selectedTracker.actionStatus === 'RESOLVED' && selectedTracker.baselineStart && (
              <div className="form-section">
                <div className="form-section-header">
                  <h3>A/B 对比基准</h3>
                </div>
                <div className="form-section-body">
                  <div className="form-row">
                    <label>基准区间</label>
                    <div className="mono" style={{ fontSize: 'var(--text-small)' }}>
                      {selectedTracker.baselineStart} ~ {selectedTracker.baselineEnd}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

/** 导出状态类型供其他页面使用 */
export type { ActionStatus };
