/**
 * 诊断结果列表页（v4.0 §6.4.1）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.4.1
 *
 * 布局结构：
 * 1. 顶部统计卡片：待诊断回路数 / 已诊断回路数 / 已生成 Tracker 数 / 待处理 Tracker 数
 * 2. FilterBar：搜索 + 预诊标签下拉 + 是否已生成 Tracker
 * 3. DataTable：回路名/节点/预诊标签/置信度/诊断时间/是否已生成 Tracker/操作
 *
 * 行点击导航到 /diagnosis/waveform?loopId=xxx
 */

import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, CheckCircle2, FileText, AlertCircle, ArrowRight } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DiagnosisLabelBadge } from '../../components/StatusBadge';
import {
  diagnosisResults,
  getDiagnosisStatsByLabel,
  getPendingDiagnoses,
} from '../../mock/diagnosis';
import type { DiagnosisLabel, DiagnosisResult } from '../../mock/types';
import { getTrackerStats } from '../../mock/tracker';

/** 顶部统计卡片 */
function StatCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: number;
  icon: typeof Clock;
  color: string;
}) {
  return (
    <div className="kpi-card">
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

export default function DiagnosisListPage() {
  const navigate = useNavigate();

  // 筛选状态
  const [search, setSearch] = useState('');
  const [labelFilter, setLabelFilter] = useState('');
  const [trackerFilter, setTrackerFilter] = useState('');

  // 统计数据
  const stats = useMemo(() => {
    const pending = getPendingDiagnoses().length;
    const diagnosed = diagnosisResults.length;
    const trackerStats = getTrackerStats();
    const withTracker = diagnosisResults.filter((d) => d.hasTracker).length;
    return {
      pending,
      diagnosed,
      withTracker,
      pendingTracker: trackerStats.pending,
    };
  }, []);

  // 预诊标签选项
  const labelOptions = useMemo(() => {
    const labels = getDiagnosisStatsByLabel().map((s) => s.label);
    return labels.map((l) => ({ label: l, value: l }));
  }, []);

  // 筛选条件
  const filters: FilterItem[] = [
    {
      key: 'label',
      label: '预诊标签',
      type: 'select',
      options: labelOptions,
      value: labelFilter,
      onChange: setLabelFilter,
    },
    {
      key: 'tracker',
      label: 'Tracker',
      type: 'select',
      options: [
        { label: '已生成', value: 'yes' },
        { label: '未生成', value: 'no' },
      ],
      value: trackerFilter,
      onChange: setTrackerFilter,
    },
  ];

  // 过滤后的数据
  const filteredData = useMemo(() => {
    return diagnosisResults.filter((d) => {
      // 搜索匹配回路名或节点
      if (search) {
        const kw = search.toLowerCase();
        if (
          !d.loopName.toLowerCase().includes(kw) &&
          !d.nodeName.toLowerCase().includes(kw) &&
          !d.loopId.toLowerCase().includes(kw)
        ) {
          return false;
        }
      }
      // 预诊标签筛选
      if (labelFilter && d.label !== labelFilter) return false;
      // Tracker 筛选
      if (trackerFilter === 'yes' && !d.hasTracker) return false;
      if (trackerFilter === 'no' && d.hasTracker) return false;
      return true;
    });
  }, [search, labelFilter, trackerFilter]);

  // 清除全部筛选
  const handleClearAll = () => {
    setSearch('');
    setLabelFilter('');
    setTrackerFilter('');
  };

  // 列定义
  const columns: Column<DiagnosisResult>[] = [
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
      header: '所属节点',
      sortable: true,
      width: '120px',
    },
    {
      key: 'label',
      header: '预诊标签',
      width: '130px',
      render: (row) => <DiagnosisLabelBadge label={row.label} />,
    },
    {
      key: 'confidence',
      header: '置信度',
      sortable: true,
      width: '140px',
      align: 'left',
      render: (row) => {
        const pct = Math.round(row.confidence * 100);
        const color = pct >= 85 ? 'var(--status-ok)' : pct >= 70 ? 'var(--status-warning)' : 'var(--status-danger)';
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <div style={{ flex: 1, height: 6, background: 'var(--bg-muted)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', background: color }} />
            </div>
            <span className="mono" style={{ fontSize: 'var(--text-small)', minWidth: 32 }}>{pct}%</span>
          </div>
        );
      },
    },
    {
      key: 'diagnosisTime',
      header: '诊断时间',
      sortable: true,
      width: '150px',
      render: (row) => <span className="mono" style={{ fontSize: 'var(--text-small)' }}>{row.diagnosisTime}</span>,
    },
    {
      key: 'hasTracker',
      header: 'Tracker',
      width: '100px',
      align: 'center',
      sortable: true,
      sortValue: (row) => (row.hasTracker ? 1 : 0),
      render: (row) =>
        row.hasTracker ? (
          <span className="badge status-success badge-sm">
            <CheckCircle2 size={12} />
            <span>已生成</span>
          </span>
        ) : (
          <span className="badge status-neutral badge-sm">
            <Clock size={12} />
            <span>未生成</span>
          </span>
        ),
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

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>诊断结果列表</h1>
          <p className="page-subtitle">
            共 {diagnosisResults.length} 条诊断结果 · 数据更新于 2026-06-21 09:30
          </p>
        </div>
      </div>

      {/* 顶部统计卡片 */}
      <div className="kpi-grid">
        <StatCard label="待诊断回路数" value={stats.pending} icon={Clock} color="var(--status-warning)" />
        <StatCard label="已诊断回路数" value={stats.diagnosed} icon={AlertCircle} color="var(--accent-blue)" />
        <StatCard label="已生成 Tracker 数" value={stats.withTracker} icon={FileText} color="var(--status-ok)" />
        <StatCard label="待处理 Tracker 数" value={stats.pendingTracker} icon={Clock} color="var(--status-danger)" />
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索回路名 / 位号 / 节点"
        filters={filters}
        showClearAll={!!(search || labelFilter || trackerFilter)}
        onClearAll={handleClearAll}
      />

      {/* 数据表格 */}
      <DataTable
        columns={columns}
        data={filteredData}
        rowKey={(row) => row.resultId}
        onRowClick={(row) => navigate(`/diagnosis/waveform?loopId=${row.loopId}`)}
        emptyText="无匹配的诊断结果"
        initialSortKey="confidence"
        initialSortDir="desc"
      />
    </div>
  );
}

/** 导出预诊标签类型供其他页面使用 */
export type { DiagnosisLabel };
