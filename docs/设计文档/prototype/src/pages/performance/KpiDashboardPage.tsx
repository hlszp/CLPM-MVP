/**
 * KPI 看板页面（UI/UX §6.3.1）
 *
 * 布局结构（自上而下）：
 * 1. Partial 警告横幅 — PARTIAL/INCONCLUSIVE 回路数
 * 2. KPI 摘要卡片 ×4 — 回路总数/平均评分/低效回路数/通过率
 * 3. 两列：7 天评分趋势 + KPI 分项雷达图
 * 4. 回路评分排行表（按评分升序）
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动（success/warning/danger）
 * - 卡片用 border + radius-md，不用左 border accent
 */

import { useMemo, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from 'echarts';
import {
  Activity,
  Gauge,
  TrendingDown,
  CheckCircle2,
  Eye,
} from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { PartialBanner } from '../../components/EmptyState';
import { ComputeStatusBadge, ScoreBadge } from '../../components/StatusBadge';
import { useRole } from '../../components/RoleContext';
import {
  kpiSnapshots,
  kpiDefinitions,
  getGlobalKpiSummary,
  getScoreTrend7d,
} from '../../mock/kpi';
import { getLoopStats } from '../../mock/loops';
import type { KpiSnapshot } from '../../mock/types';

/** 带排名的快照类型 */
type RankedSnapshot = KpiSnapshot & { rank: number };

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

/** 7 天评分趋势图（折线 + 柱状图） */
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

  return <div ref={chartRef} style={{ width: '100%', height: '240px' }} />;
}

/** KPI 分项雷达图（6 大 KPI 维度全厂平均） */
function KpiRadarChart() {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

  /** 计算各 KPI 维度全厂平均得分 */
  const radarValues = useMemo(() => {
    return kpiDefinitions.map((kpi) => {
      const scores = kpiSnapshots
        .filter((s) => s.computeStatus !== 'INCONCLUSIVE')
        .map((s) => s.items.find((i) => i.kpiId === kpi.kpiId)?.score)
        .filter((s): s is number => s !== null && s !== undefined);
      const avg = scores.reduce((sum, s) => sum + s, 0) / (scores.length || 1);
      return Math.round(avg * 10) / 10;
    });
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    const option: echarts.EChartsOption = {
      tooltip: {
        formatter: (params: unknown) => {
          const p = params as { value: number[]; name: string };
          if (!p || !p.value) return '';
          let html = `<div style="font-size:12px;font-weight:600;margin-bottom:4px">${p.name}</div>`;
          kpiDefinitions.forEach((kpi, idx) => {
            html += `<div style="font-size:11px;color:#6C757D">${kpi.kpiName}: <strong>${p.value[idx]}</strong></div>`;
          });
          return html;
        },
      },
      radar: {
        center: ['50%', '55%'],
        radius: '65%',
        indicator: kpiDefinitions.map((k) => ({
          name: k.kpiCode,
          max: 100,
        })),
        shape: 'polygon',
        splitNumber: 4,
        axisName: { color: '#6C757D', fontSize: 11 },
        splitLine: { lineStyle: { color: '#E5E5E5' } },
        splitArea: { areaStyle: { color: ['#F8F9FA', '#FFFFFF'] } },
        axisLine: { lineStyle: { color: '#E5E5E5' } },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: radarValues,
              name: '全厂平均',
              lineStyle: { color: '#0D6EFD', width: 2 },
              itemStyle: { color: '#0D6EFD' },
              areaStyle: { color: 'rgba(13, 110, 253, 0.15)' },
            },
          ],
        },
      ],
    };

    chartInstance.current.setOption(option, true);
  }, [radarValues]);

  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, []);

  return <div ref={chartRef} style={{ width: '100%', height: '240px' }} />;
}

/** KPI 分项得分单元格（带颜色） */
function KpiScoreCell({ score }: { score: number | null }) {
  if (score === null) return <span className="text-muted">—</span>;
  const color = score >= 80 ? '#198754' : score >= 60 ? '#B8860B' : '#DC3545';
  return (
    <span className="mono" style={{ color, fontWeight: 600 }}>
      {score}
    </span>
  );
}

export default function KpiDashboardPage() {
  const navigate = useNavigate();
  const { role } = useRole();

  const stats = useMemo(() => getLoopStats(), []);
  const summary = useMemo(() => getGlobalKpiSummary(), []);

  /** 按评分升序排列的回路快照（带排名） */
  const rankedSnapshots = useMemo<RankedSnapshot[]>(() => {
    return [...kpiSnapshots]
      .sort((a, b) => {
        if (a.score === null) return 1;
        if (b.score === null) return -1;
        return a.score - b.score;
      })
      .map((s, idx) => ({ ...s, rank: idx + 1 }));
  }, []);

  /** 通过率：评分 >= 60 的回路占比 */
  const passRate = useMemo(() => {
    const passed = summary.excellent + summary.warning;
    return Math.round((passed / summary.total) * 1000) / 10;
  }, [summary]);

  /** 排行表列定义 */
  const columns: Column<RankedSnapshot>[] = useMemo(() => {
    const kpiColumns: Column<RankedSnapshot>[] = kpiDefinitions.map((kpi) => ({
      key: `kpi_${kpi.kpiId}`,
      header: kpi.kpiCode,
      width: '70px',
      align: 'center',
      render: (row) => {
        const item = row.items.find((i) => i.kpiId === kpi.kpiId);
        return <KpiScoreCell score={item?.score ?? null} />;
      },
    }));

    return [
      {
        key: 'rank',
        header: '排名',
        width: '50px',
        align: 'center',
        sortable: true,
        sortValue: (row) => row.rank,
        render: (row) => (
          <span
            className="mono"
            style={{
              fontWeight: 600,
              color: row.rank <= 3 ? '#DC3545' : '#6C757D',
            }}
          >
            {row.rank}
          </span>
        ),
      },
      {
        key: 'loopName',
        header: '回路名',
        sortable: true,
        render: (row) => <span style={{ fontWeight: 500 }}>{row.loopName}</span>,
      },
      {
        key: 'nodeName',
        header: '节点',
        width: '100px',
        render: (row) => <span className="text-muted">{row.nodeName}</span>,
      },
      {
        key: 'score',
        header: '评分',
        width: '70px',
        align: 'center',
        sortable: true,
        sortValue: (row) => row.score ?? -1,
        render: (row) => <ScoreBadge score={row.score} />,
      },
      ...kpiColumns,
      {
        key: 'computeStatus',
        header: '计算状态',
        width: '90px',
        align: 'center',
        render: (row) => <ComputeStatusBadge status={row.computeStatus} />,
      },
      {
        key: 'action',
        header: '操作',
        width: '80px',
        align: 'center',
        render: (row) => (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: '12px' }}
            onClick={(e) => {
              e.stopPropagation();
              navigate(`/loop/monitor?loopId=${row.loopId}`);
            }}
          >
            <Eye size={12} /> 查看
          </button>
        ),
      },
    ];
  }, [navigate]);

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>KPI 看板</h1>
          <p className="page-subtitle">
            2026-06-21 10:30 · 加氢联合车间 · 数据快照 10:00 · 当前视角：{role}
          </p>
        </div>
      </div>

      {/* Partial 警告横幅 */}
      <PartialBanner
        count={summary.partial + summary.inconclusive}
        onAction={() => navigate('/performance/history')}
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
          value={summary.avgScore}
          icon={Gauge}
          color="#198754"
          delta={`优秀 ${summary.excellent} · 警告 ${summary.warning} · 低效 ${summary.low}`}
        />
        <KpiCard
          label="低效回路数（<60分）"
          value={summary.low}
          icon={TrendingDown}
          color="#DC3545"
          delta={`占回路总数 ${Math.round((summary.low / summary.total) * 100)}%`}
        />
        <KpiCard
          label="通过率（≥60分）"
          value={`${passRate}%`}
          icon={CheckCircle2}
          color="#0D6EFD"
          delta={`优秀 ${summary.excellent} · 警告 ${summary.warning}`}
        />
      </div>

      {/* 两列：趋势图 + 雷达图 */}
      <div className="two-col-grid">
        {/* 7 天评分趋势 */}
        <div className="card">
          <div className="card-header">
            <h3>7 天评分趋势</h3>
          </div>
          <div className="card-body">
            <ScoreTrendChart />
          </div>
        </div>

        {/* KPI 分项雷达图 */}
        <div className="card">
          <div className="card-header">
            <h3>KPI 分项雷达图（全厂平均）</h3>
          </div>
          <div className="card-body">
            <KpiRadarChart />
          </div>
        </div>
      </div>

      {/* 回路评分排行表 */}
      <div className="card">
        <div className="card-header">
          <h3>回路评分排行（按评分升序）</h3>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={rankedSnapshots}
            rowKey={(row) => row.loopId}
            onRowClick={(row) => navigate(`/loop/monitor?loopId=${row.loopId}`)}
            initialSortKey="score"
            initialSortDir="asc"
          />
        </div>
      </div>
    </div>
  );
}
