/**
 * 工作台页面（v4.0 §6.1）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.1 + PRD v3.0 §3.1 + FDS v3.0 §4.1
 *
 * 布局结构（自上而下）：
 * 1. Partial 警告横幅（§6.1.1）— PARTIAL 回路数 + 查看详情链接
 * 2. KPI 摘要卡片 ×4（§6.1.2）— 回路总数/平均评分/低效回路/PV质量异常
 * 3. 两列：7天评分趋势 + 低效回路 Top 5（§6.1.3）
 * 4. 两列：诊断标签分布 + 异常跟踪摘要（§6.1.4）
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动（success/warning/danger）
 * - 高密度信息展示，每屏 ≥3 处产品差异化信息
 * - 卡片用 border + radius，不用左 border accent
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';
import {
  Activity,
  Gauge,
  TrendingDown,
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock,
  XCircle,
} from 'lucide-react';
import { PVQualityBadge } from '../components/PVQualityBadge';
import { ComputeStatusBadge } from '../components/StatusBadge';
import { PartialBanner } from '../components/EmptyState';
import { useRole } from '../components/RoleContext';
import {
  getLoopStats,
  getLoopsByScoreAsc,
} from '../mock/loops';
import {
  getGlobalKpiSummary,
  getScoreTrend7d,
} from '../mock/kpi';
import { getDiagnosisStatsByLabel } from '../mock/diagnosis';
import { getTrackerStats, getClosureTimeStats } from '../mock/tracker';

/** KPI 摘要卡片 */
function KpiCard({
  label,
  value,
  icon: Icon,
  color,
  delta,
}: {
  label: string;
  value: string | number;
  icon: React.ComponentType<{ size?: number; className?: string; style?: React.CSSProperties }>;
  color: string;
  delta?: string;
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
      {delta && <div className="kpi-card-delta">{delta}</div>}
    </div>
  );
}

/** 7 天评分趋势图（ECharts 内联配置） */
function ScoreTrendChart() {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const trend = getScoreTrend7d();

  useEffect(() => {
    if (!chartRef.current) return;
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const arr = params as Array<{ axisValue: string; value: number; seriesName: string; color: string }>;
          if (!arr || arr.length === 0) return '';
          let html = `<div style="font-size:12px;color:#6C757D">${arr[0].axisValue}</div>`;
          arr.forEach((p) => {
            html += `<div style="display:flex;align-items:center;gap:6px;margin-top:2px">
              <span style="display:inline-block;width:8px;height:8px;background:${p.color};border-radius:50%"></span>
              <span>${p.seriesName}: <strong>${p.value}</strong></span>
            </div>`;
          });
          return html;
        },
      },
      legend: {
        top: 0,
        right: 10,
        textStyle: { fontSize: 12 },
      },
      grid: { left: 40, right: 20, top: 35, bottom: 30 },
      xAxis: {
        type: 'category',
        data: trend.map((t) => t.date),
        axisLabel: { fontSize: 11, color: '#6C757D' },
        axisLine: { lineStyle: { color: '#E0E0E0' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 11, color: '#6C757D' },
        splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      },
      series: [
        {
          name: '平均评分',
          type: 'line',
          data: trend.map((t) => t.avg),
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#0D6EFD', width: 2 },
          itemStyle: { color: '#0D6EFD' },
          areaStyle: { color: 'rgba(13, 110, 253, 0.08)' },
        },
        {
          name: '低效回路数',
          type: 'bar',
          data: trend.map((t) => t.low),
          barWidth: 16,
          itemStyle: { color: 'rgba(220, 53, 69, 0.3)', borderRadius: [2, 2, 0, 0] },
        },
      ],
    };

    chartInstance.current.setOption(option, true);
  }, [trend]);

  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, []);

  return <div ref={chartRef} style={{ width: '100%', height: '200px' }} />;
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { role } = useRole();

  const stats = useMemo(() => getLoopStats(), []);
  const kpiSummary = useMemo(() => getGlobalKpiSummary(), []);
  const lowPerfLoops = useMemo(() => getLoopsByScoreAsc().slice(0, 5), []);
  const diagStats = useMemo(() => getDiagnosisStatsByLabel(), []);
  const trackerStats = useMemo(() => getTrackerStats(), []);
  const closureStats = useMemo(() => getClosureTimeStats(), []);

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>工作台</h1>
          <p className="page-subtitle">
            2026-06-21 10:30 · 加氢联合车间 · 数据快照 10:00 · 当前视角：{role}
          </p>
        </div>
      </div>

      {/* Partial 警告横幅 */}
      <PartialBanner
        count={stats.partial + stats.inconclusive}
        onAction={() => navigate('/performance')}
      />

      {/* KPI 摘要卡片 */}
      <div className="kpi-grid">
        <KpiCard
          label="回路总数"
          value={stats.total}
          icon={Activity}
          color="#1A1A1A"
          delta={`自动 ${stats.total - stats.manualMode} · 手动 ${stats.manualMode}`}
        />
        <KpiCard
          label="平均评分"
          value={stats.avgScore}
          icon={Gauge}
          color="#198754"
          delta={`优秀 ${kpiSummary.excellent} · 警告 ${kpiSummary.warning} · 低效 ${kpiSummary.low}`}
        />
        <KpiCard
          label="低效回路（<60分）"
          value={stats.lowPerf}
          icon={TrendingDown}
          color="#DC3545"
          delta={`占回路总数 ${Math.round((stats.lowPerf / stats.total) * 100)}%`}
        />
        <KpiCard
          label="PV 质量异常"
          value={stats.pvBad + stats.pvUncertain}
          icon={AlertCircle}
          color="#FFC107"
          delta={`Bad ${stats.pvBad} · Uncertain ${stats.pvUncertain}`}
        />
      </div>

      {/* 两列：趋势 + 低效排行 */}
      <div className="two-col-grid">
        {/* 7 天评分趋势 */}
        <div className="card">
          <div className="card-header">
            <h3>7 天评分趋势</h3>
            <button
              type="button"
              className="card-more-link"
              onClick={() => navigate('/performance')}
            >
              KPI 看板 <ArrowRight size={12} />
            </button>
          </div>
          <div className="card-body">
            <ScoreTrendChart />
          </div>
        </div>

        {/* 低效回路 Top 5 */}
        <div className="card">
          <div className="card-header">
            <h3>低效回路 Top 5</h3>
            <button
              type="button"
              className="card-more-link"
              onClick={() => navigate('/loop/monitor')}
            >
              全部回路 <ArrowRight size={12} />
            </button>
          </div>
          <div className="card-body">
            <ul className="loop-rank-list">
              {lowPerfLoops.map((loop, idx) => (
                <li
                  key={loop.loopId}
                  className="loop-rank-item"
                  onClick={() => navigate(`/loop/monitor?loopId=${loop.loopId}`)}
                >
                  <span className={`loop-rank-num ${idx < 3 ? 'top' : ''}`}>{idx + 1}</span>
                  <div className="loop-rank-info">
                    <span className="loop-rank-name">{loop.loopName}</span>
                    <span className="loop-rank-node">{loop.nodeName}</span>
                  </div>
                  <PVQualityBadge quality={loop.pvQuality} size="sm" />
                  <ComputeStatusBadge status={loop.computeStatus} size="sm" />
                  <span className={`loop-rank-score ${loop.score !== null && loop.score < 60 ? 'low' : ''}`}>
                    {loop.score ?? '—'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* 两列：诊断摘要 + 异常跟踪 */}
      <div className="two-col-grid">
        {/* 诊断标签分布 */}
        <div className="card">
          <div className="card-header">
            <h3>诊断标签分布</h3>
            <button
              type="button"
              className="card-more-link"
              onClick={() => navigate('/diagnosis')}
            >
              诊断中心 <ArrowRight size={12} />
            </button>
          </div>
          <div className="card-body">
            <div className="diag-stats-list">
              {diagStats.map((item) => {
                const maxCount = Math.max(...diagStats.map((d) => d.count));
                const widthPct = (item.count / maxCount) * 100;
                const color = getDiagColor(item.label);
                return (
                  <div key={item.label} className="diag-stat-row">
                    <span className="diag-stat-label">{item.label}</span>
                    <div className="diag-stat-bar-bg">
                      <div
                        className="diag-stat-bar-fill"
                        style={{ width: `${widthPct}%`, background: color }}
                      />
                    </div>
                    <span className="diag-stat-count">{item.count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 异常跟踪摘要 */}
        <div className="card">
          <div className="card-header">
            <h3>异常跟踪摘要</h3>
            <button
              type="button"
              className="card-more-link"
              onClick={() => navigate('/diagnosis/tracker')}
            >
              全部 Tracker <ArrowRight size={12} />
            </button>
          </div>
          <div className="card-body">
            <div className="tracker-summary-grid">
              <div className="tracker-stat-item pending">
                <Clock size={18} />
                <span className="tracker-stat-num">{trackerStats.pending}</span>
                <span className="tracker-stat-label">待处理</span>
              </div>
              <div className="tracker-stat-item progress">
                <Activity size={18} />
                <span className="tracker-stat-num">{trackerStats.inProgress}</span>
                <span className="tracker-stat-label">进行中</span>
              </div>
              <div className="tracker-stat-item resolved">
                <CheckCircle2 size={18} />
                <span className="tracker-stat-num">{trackerStats.resolved}</span>
                <span className="tracker-stat-label">已解决</span>
              </div>
              <div className="tracker-stat-item ignored">
                <XCircle size={18} />
                <span className="tracker-stat-num">{trackerStats.ignored}</span>
                <span className="tracker-stat-label">已忽略</span>
              </div>
            </div>
            {closureStats.length > 0 && (
              <div className="tracker-closure-note">
                <strong>最近闭环：</strong>
                {closureStats.map((c) => (
                  <span key={c.trackerId}>
                    {c.loopName} · 闭环时长 {c.hours}h
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 诊断标签颜色映射 */
function getDiagColor(label: string): string {
  const map: Record<string, string> = {
    '振荡': '#DC3545',
    '粘滞阀': '#FFC107',
    '参数过激': '#FFC107',
    '参数过保守': '#6C757D',
    '外扰频繁': '#6C757D',
    'PV 质量异常': '#DC3545',
    '人工复核': '#0DCAF0',
  };
  return map[label] ?? '#6C757D';
}
