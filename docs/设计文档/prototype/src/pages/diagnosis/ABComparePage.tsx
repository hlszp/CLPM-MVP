/**
 * A/B 对比页（v4.0 §6.4.5）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.4.5
 *
 * 布局结构：
 * 1. FilterBar：选择已解决的 Tracker（下拉）+ 时间范围
 * 2. 顶部摘要：调整前平均分 → 调整后平均分（用箭头和颜色标识改善）
 * 3. 中部两列布局：左侧调整前波形 / 右侧调整后波形
 * 4. 底部 KPI 对比表（DataTable）：KPI 指标/调整前得分/调整后得分/变化幅度/改善率
 */

import { useState, useMemo } from 'react';
import { ArrowRight, TrendingUp, TrendingDown } from 'lucide-react';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DataTable, type Column } from '../../components/DataTable';
import { WaveformChart } from '../../components/WaveformChart';
import { EmptyState } from '../../components/EmptyState';
import { actionTrackers } from '../../mock/tracker';
import { getABComparison } from '../../mock/timeseries';
import type { ActionTracker, TimeseriesDataset } from '../../mock/types';

/** KPI 对比行 */
interface KpiCompareRow {
  kpiName: string;
  beforeScore: number;
  afterScore: number;
  delta: number;
  improveRate: number;
}

export default function ABComparePage() {
  // 选中的 Tracker ID
  const [selectedTrackerId, setSelectedTrackerId] = useState('');
  const [timeRange, setTimeRange] = useState('7d');

  // 已解决的 Tracker 列表
  const resolvedTrackers = useMemo(
    () => actionTrackers.filter((t) => t.actionStatus === 'RESOLVED'),
    [],
  );

  // 默认选中第一个已解决的 Tracker
  const effectiveTrackerId = selectedTrackerId || (resolvedTrackers[0]?.trackerId ?? '');

  // 选中的 Tracker
  const selectedTracker = useMemo<ActionTracker | undefined>(
    () => actionTrackers.find((t) => t.trackerId === effectiveTrackerId),
    [effectiveTrackerId],
  );

  // A/B 对比数据
  const comparison = useMemo(() => {
    if (!selectedTracker || !selectedTracker.baselineStart || !selectedTracker.baselineEnd) return null;
    return getABComparison(selectedTracker.loopId, selectedTracker.baselineStart, selectedTracker.baselineEnd);
  }, [selectedTracker]);

  // 构造调整前/调整后数据集
  const beforeDataset: TimeseriesDataset | null = useMemo(() => {
    if (!comparison || !selectedTracker) return null;
    const pts = comparison.before.points;
    return {
      loopId: selectedTracker.loopId,
      loopName: `${selectedTracker.loopName}（调整前）`,
      points: pts,
      windowStart: pts[0]?.timestamp ?? 0,
      windowEnd: pts[pts.length - 1]?.timestamp ?? 0,
      sampleCount: pts.length,
    };
  }, [comparison, selectedTracker]);

  const afterDataset: TimeseriesDataset | null = useMemo(() => {
    if (!comparison || !selectedTracker) return null;
    const pts = comparison.after.points;
    return {
      loopId: selectedTracker.loopId,
      loopName: `${selectedTracker.loopName}（调整后）`,
      points: pts,
      windowStart: pts[0]?.timestamp ?? 0,
      windowEnd: pts[pts.length - 1]?.timestamp ?? 0,
      sampleCount: pts.length,
    };
  }, [comparison, selectedTracker]);

  // KPI 对比数据（基于 mock 平均分派生）
  const kpiRows: KpiCompareRow[] = useMemo(() => {
    if (!comparison) return [];
    const beforeAvg = comparison.before.avgScore;
    const afterAvg = comparison.after.avgScore;
    // 派生 5 项 KPI 对比数据
    return [
      { kpiName: '综合评分', beforeScore: beforeAvg, afterScore: afterAvg, delta: afterAvg - beforeAvg, improveRate: Math.round(((afterAvg - beforeAvg) / beforeAvg) * 100) },
      { kpiName: '平稳性', beforeScore: Math.round(beforeAvg * 0.9), afterScore: Math.round(afterAvg * 0.95), delta: Math.round(afterAvg * 0.95 - beforeAvg * 0.9), improveRate: Math.round(((afterAvg * 0.95 - beforeAvg * 0.9) / (beforeAvg * 0.9)) * 100) },
      { kpiName: '响应性', beforeScore: Math.round(beforeAvg * 0.85), afterScore: Math.round(afterAvg * 0.92), delta: Math.round(afterAvg * 0.92 - beforeAvg * 0.85), improveRate: Math.round(((afterAvg * 0.92 - beforeAvg * 0.85) / (beforeAvg * 0.85)) * 100) },
      { kpiName: '鲁棒性', beforeScore: Math.round(beforeAvg * 1.05), afterScore: Math.round(afterAvg * 1.02), delta: Math.round(afterAvg * 1.02 - beforeAvg * 1.05), improveRate: Math.round(((afterAvg * 1.02 - beforeAvg * 1.05) / (beforeAvg * 1.05)) * 100) },
      { kpiName: '能耗', beforeScore: Math.round(beforeAvg * 0.8), afterScore: Math.round(afterAvg * 0.88), delta: Math.round(afterAvg * 0.88 - beforeAvg * 0.8), improveRate: Math.round(((afterAvg * 0.88 - beforeAvg * 0.8) / (beforeAvg * 0.8)) * 100) },
    ];
  }, [comparison]);

  // 筛选项
  const filters: FilterItem[] = [
    {
      key: 'tracker',
      label: '已解决 Tracker',
      type: 'select',
      options: resolvedTrackers.map((t) => ({
        label: `${t.trackerId} · ${t.loopName}`,
        value: t.trackerId,
      })),
      value: effectiveTrackerId,
      onChange: setSelectedTrackerId,
    },
    {
      key: 'range',
      label: '时间范围',
      type: 'select',
      options: [
        { label: '近 7 天', value: '7d' },
        { label: '近 14 天', value: '14d' },
        { label: '近 30 天', value: '30d' },
      ],
      value: timeRange,
      onChange: setTimeRange,
    },
  ];

  // KPI 对比表列定义
  const columns: Column<KpiCompareRow>[] = [
    {
      key: 'kpiName',
      header: 'KPI 指标',
      sortable: true,
      width: '160px',
      render: (row) => <span style={{ fontWeight: 500 }}>{row.kpiName}</span>,
    },
    {
      key: 'beforeScore',
      header: '调整前得分',
      sortable: true,
      width: '120px',
      align: 'center',
      render: (row) => <span className="mono">{row.beforeScore}</span>,
    },
    {
      key: 'afterScore',
      header: '调整后得分',
      sortable: true,
      width: '120px',
      align: 'center',
      render: (row) => <span className="mono" style={{ fontWeight: 600, color: 'var(--status-ok)' }}>{row.afterScore}</span>,
    },
    {
      key: 'delta',
      header: '变化幅度',
      sortable: true,
      width: '120px',
      align: 'center',
      render: (row) => {
        const isImprove = row.delta > 0;
        const Icon = isImprove ? TrendingUp : TrendingDown;
        const color = isImprove ? 'var(--status-ok)' : 'var(--status-danger)';
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)', color }}>
            <Icon size={14} />
            <span className="mono">{row.delta > 0 ? '+' : ''}{row.delta}</span>
          </span>
        );
      },
    },
    {
      key: 'improveRate',
      header: '改善率',
      sortable: true,
      width: '120px',
      align: 'center',
      sortValue: (row) => row.improveRate,
      render: (row) => {
        const isImprove = row.improveRate > 0;
        const color = isImprove ? 'var(--status-ok)' : 'var(--status-danger)';
        return (
          <span className="mono" style={{ color, fontWeight: 600 }}>
            {row.improveRate > 0 ? '+' : ''}{row.improveRate}%
          </span>
        );
      },
    },
  ];

  // 无已解决 Tracker 时显示空状态
  if (resolvedTrackers.length === 0) {
    return (
      <div className="page-container">
        <div className="page-header">
          <div>
            <h1>A/B 对比</h1>
            <p className="page-subtitle">对比 Tracker 处理前后的回路性能指标</p>
          </div>
        </div>
        <div className="page-empty-state">
          <EmptyState
            type="empty"
            title="暂无可对比的 Tracker"
            description='需要至少一个状态为"已解决"的 Tracker 才能进行 A/B 对比'
          />
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>A/B 对比</h1>
          <p className="page-subtitle">
            对比 Tracker 处理前后的回路性能指标 · 共 {resolvedTrackers.length} 个已解决 Tracker
          </p>
        </div>
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue=""
        onSearchChange={() => {}}
        searchPlaceholder=""
        filters={filters}
      />

      {comparison && selectedTracker && (
        <>
          {/* 顶部摘要：调整前 → 调整后 */}
          <div className="card" style={{ marginBottom: 'var(--space-4)' }}>
            <div className="card-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 'var(--space-6)', flexWrap: 'wrap' }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)', marginBottom: 'var(--space-1)' }}>调整前平均分</div>
                <div className="mono" style={{ fontSize: 32, fontWeight: 700, color: 'var(--status-danger)' }}>
                  {comparison.before.avgScore}
                </div>
                <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)', marginTop: 'var(--space-1)' }}>
                  {comparison.before.period}
                </div>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 'var(--space-1)' }}>
                <ArrowRight size={32} color="var(--accent-blue)" />
                <span
                  className="mono"
                  style={{
                    fontSize: 'var(--text-small)',
                    fontWeight: 600,
                    color: 'var(--status-ok)',
                    background: 'rgba(25, 135, 84, 0.12)',
                    padding: '2px var(--space-2)',
                    borderRadius: 'var(--radius-pill)',
                  }}
                >
                  +{comparison.after.avgScore - comparison.before.avgScore} 分
                </span>
              </div>

              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)', marginBottom: 'var(--space-1)' }}>调整后平均分</div>
                <div className="mono" style={{ fontSize: 32, fontWeight: 700, color: 'var(--status-ok)' }}>
                  {comparison.after.avgScore}
                </div>
                <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)', marginTop: 'var(--space-1)' }}>
                  {comparison.after.period}
                </div>
              </div>
            </div>
          </div>

          {/* 双波形对比 */}
          <div className="two-col-grid">
            <div className="card">
              <div className="card-header">
                <h3>调整前波形</h3>
                <span className="badge status-danger badge-sm">调整前</span>
              </div>
              <div className="card-body">
                {beforeDataset && (
                  <WaveformChart dataset={beforeDataset} height="280px" showDataZoom={false} showOp={true} />
                )}
              </div>
            </div>

            <div className="card">
              <div className="card-header">
                <h3>调整后波形</h3>
                <span className="badge status-success badge-sm">调整后</span>
              </div>
              <div className="card-body">
                {afterDataset && (
                  <WaveformChart dataset={afterDataset} height="280px" showDataZoom={false} showOp={true} />
                )}
              </div>
            </div>
          </div>

          {/* Tracker 处理说明 */}
          <div className="card" style={{ marginBottom: 'var(--space-4)' }}>
            <div className="card-header">
              <h3>处理说明</h3>
              <span className="mono" style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)' }}>
                {selectedTracker.trackerId} · {selectedTracker.loopName}
              </span>
            </div>
            <div className="card-body">
              <p style={{ margin: 0, lineHeight: 1.6, color: 'var(--text-primary)' }}>
                {selectedTracker.comment || '无处理说明'}
              </p>
            </div>
          </div>

          {/* KPI 对比表 */}
          <div className="card">
            <div className="card-header">
              <h3>KPI 对比表</h3>
            </div>
            <div className="card-body" style={{ padding: 0 }}>
              <DataTable
                columns={columns}
                data={kpiRows}
                rowKey={(row) => row.kpiName}
                initialSortKey="improveRate"
                initialSortDir="desc"
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
