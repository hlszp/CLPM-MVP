/**
 * 整定效果统计页面（Phase 2 §6.5.5）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.5.5
 *
 * 布局结构：
 * 1. 顶部：Phase 2 标签
 * 2. 中部：4 个统计卡片（累计整定次数/平均改善率/最佳改善案例/待复评回路数）
 * 3. 中下：整定效果趋势图（ECharts 折线图，月度整定次数 + 平均改善率）
 * 4. 底部：整定记录表（DataTable），列：时间/回路名/整定算法/调整前评分/调整后评分/改善率/操作
 *    - 行点击：打开 Drawer 显示整定详情
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 卡片用 border + radius-md，不用左 border accent
 */

import { useState, useMemo, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import {
  FlaskConical,
  History,
  TrendingUp,
  Award,
  AlertCircle,
  ArrowRight,
} from 'lucide-react';
import { EmptyState } from '../../components/EmptyState';
import { DataTable, type Column } from '../../components/DataTable';
import { Drawer } from '../../components/Drawer';
import { useToast } from '../../components/Toast';

/** Phase 2 原型标签 */
function Phase2Tag() {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '2px 10px',
        fontSize: '12px',
        fontWeight: 600,
        color: '#B8860B',
        background: 'rgba(255, 193, 7, 0.15)',
        border: '1px solid rgba(255, 193, 7, 0.4)',
        borderRadius: '999px',
      }}
    >
      <FlaskConical size={12} />
      Phase 2 原型
    </span>
  );
}

/** 统计卡片数据 */
interface StatCard {
  label: string;
  value: string | number;
  icon: typeof History;
  color: string;
  delta: string;
}

/** 整定记录行 */
interface TuningRecord {
  id: string;
  time: string;
  loopName: string;
  algorithm: string;
  scoreBefore: number;
  scoreAfter: number;
  improvement: number;
  operator: string;
  remark: string;
}

/** 整定记录 mock 数据 */
const TUNING_RECORDS: TuningRecord[] = [
  { id: 'T001', time: '2026-06-20 14:30', loopName: 'F-101 加热炉出口温度', algorithm: 'IMC', scoreBefore: 45, scoreAfter: 78, improvement: 73, operator: '张工', remark: '参数过激导致振荡，IMC 整定后振荡消除' },
  { id: 'T002', time: '2026-06-19 10:15', loopName: 'C-202 回流量', algorithm: 'Lambda', scoreBefore: 42, scoreAfter: 75, improvement: 79, operator: '李工', remark: '参数过激，Lambda 整定降低超调' },
  { id: 'T003', time: '2026-06-18 09:00', loopName: 'R-201 反应器床层温度', algorithm: 'Ziegler-Nichols', scoreBefore: 52, scoreAfter: 70, improvement: 35, operator: '王工', remark: '粘滞阀影响，Z-N 整定部分改善' },
  { id: 'T004', time: '2026-06-17 16:20', loopName: 'C-102 回流量', algorithm: 'Cohen-Coon', scoreBefore: 68, scoreAfter: 82, improvement: 21, operator: '张工', remark: '参数过保守，Cohen-Coon 提升响应速度' },
  { id: 'T005', time: '2026-06-16 11:45', loopName: 'C-202 塔顶温度', algorithm: 'IMC', scoreBefore: 75, scoreAfter: 90, improvement: 20, operator: '李工', remark: '微调优化，进一步提升平稳性' },
  { id: 'T006', time: '2026-06-15 14:00', loopName: 'R-101 反应器入口温度', algorithm: 'Lambda', scoreBefore: 72, scoreAfter: 85, improvement: 18, operator: '王工', remark: 'Lambda 整定提升鲁棒性' },
];

/** 月度趋势数据 */
const TREND_DATA = [
  { month: '2026-01', count: 8, improvement: 32 },
  { month: '2026-02', count: 12, improvement: 38 },
  { month: '2026-03', count: 10, improvement: 45 },
  { month: '2026-04', count: 15, improvement: 42 },
  { month: '2026-05', count: 18, improvement: 48 },
  { month: '2026-06', count: 6, improvement: 41 },
];

export default function TuningStatsPage() {
  const toast = useToast();
  const [selectedRecord, setSelectedRecord] = useState<TuningRecord | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  /** 统计卡片数据 */
  const stats: StatCard[] = useMemo(() => [
    { label: '累计整定次数', value: 69, icon: History, color: '#0D6EFD', delta: '本月 6 次' },
    { label: '平均改善率', value: '38.5%', icon: TrendingUp, color: '#198754', delta: '较上月 +3.2%' },
    { label: '最佳改善案例', value: '79%', icon: Award, color: '#FFC107', delta: 'C-202 回流量' },
    { label: '待复评回路数', value: 3, icon: AlertCircle, color: '#DC3545', delta: '需 30 天复评' },
  ], []);

  /** 趋势图 ref */
  const trendChartRef = useRef<HTMLDivElement>(null);
  const trendChartInstance = useRef<echarts.ECharts | null>(null);

  /** 渲染趋势图 */
  useEffect(() => {
    if (!trendChartRef.current) return;
    if (!trendChartInstance.current) {
      trendChartInstance.current = echarts.init(trendChartRef.current);
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
      legend: { top: 0, right: 10, textStyle: { fontSize: 12 } },
      grid: { left: 50, right: 50, top: 35, bottom: 30 },
      xAxis: {
        type: 'category',
        data: TREND_DATA.map((t) => t.month),
        axisLabel: { fontSize: 11, color: '#6C757D' },
        axisLine: { lineStyle: { color: '#E0E0E0' } },
      },
      yAxis: [
        {
          type: 'value',
          name: '整定次数',
          nameTextStyle: { fontSize: 11, color: '#6C757D' },
          axisLabel: { fontSize: 11, color: '#6C757D' },
          splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
        },
        {
          type: 'value',
          name: '改善率 (%)',
          nameTextStyle: { fontSize: 11, color: '#6C757D' },
          axisLabel: { fontSize: 11, color: '#6C757D', formatter: '{value}%' },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '整定次数',
          type: 'bar',
          data: TREND_DATA.map((t) => t.count),
          barWidth: 20,
          itemStyle: { color: 'rgba(13, 110, 253, 0.6)', borderRadius: [2, 2, 0, 0] },
        },
        {
          name: '平均改善率',
          type: 'line',
          yAxisIndex: 1,
          data: TREND_DATA.map((t) => t.improvement),
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#198754', width: 2 },
          itemStyle: { color: '#198754' },
        },
      ],
    };

    trendChartInstance.current.setOption(option, true);
  }, []);

  /** 响应式调整 */
  useEffect(() => {
    const handleResize = () => trendChartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      trendChartInstance.current?.dispose();
      trendChartInstance.current = null;
    };
  }, []);

  /** 整定记录表列定义 */
  const columns: Column<TuningRecord>[] = [
    {
      key: 'time',
      header: '时间',
      sortable: true,
      width: '150px',
      render: (row) => <span className="mono" style={{ fontSize: '12px' }}>{row.time}</span>,
    },
    {
      key: 'loopName',
      header: '回路名',
      sortable: true,
      render: (row) => <strong>{row.loopName}</strong>,
    },
    {
      key: 'algorithm',
      header: '整定算法',
      align: 'center',
      render: (row) => (
        <span className="badge status-info badge-sm">{row.algorithm}</span>
      ),
    },
    {
      key: 'scoreBefore',
      header: '调整前评分',
      sortable: true,
      align: 'center',
      render: (row) => (
        <span className="mono" style={{ color: '#DC3545', fontWeight: 600 }}>{row.scoreBefore}</span>
      ),
    },
    {
      key: 'scoreAfter',
      header: '调整后评分',
      sortable: true,
      align: 'center',
      render: (row) => (
        <span className="mono" style={{ color: '#198754', fontWeight: 600 }}>{row.scoreAfter}</span>
      ),
    },
    {
      key: 'improvement',
      header: '改善率',
      sortable: true,
      align: 'center',
      render: (row) => {
        const color = row.improvement >= 50 ? '#198754' : row.improvement >= 30 ? '#FFC107' : '#6C757D';
        return (
          <span className="mono" style={{ color, fontWeight: 600 }}>
            +{row.improvement}%
          </span>
        );
      },
    },
    {
      key: 'action',
      header: '操作',
      align: 'center',
      render: () => (
        <span style={{ color: 'var(--accent-blue)', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
          查看详情 <ArrowRight size={12} />
        </span>
      ),
    },
  ];

  /** 行点击打开 Drawer */
  const handleRowClick = (row: TuningRecord) => {
    setSelectedRecord(row);
    setDrawerOpen(true);
  };

  /** Drawer 中查看复评 */
  const handleReview = () => {
    toast.warning('Phase 2 功能，暂不可用');
  };

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            整定效果统计
            <Phase2Tag />
          </h1>
          <p className="page-subtitle">
            Phase 2 功能 · 整定前后 KPI 对比 · 风险说明（不下写 DCS）
          </p>
        </div>
      </div>

      {/* Phase 2 原型提示 */}
      <EmptyState
        type="partial"
        title="Phase 2 功能，当前为原型演示"
        description="整定效果统计为 Phase 2 功能，当前页面仅展示 UI 原型与统计数据展示形式，实际整定记录暂不可用。"
      />

      {/* 统计卡片 */}
      <div className="kpi-grid" style={{ marginTop: '16px' }}>
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div className="kpi-card" key={stat.label}>
              <div className="kpi-card-header">
                <span className="kpi-card-label">{stat.label}</span>
                <Icon size={16} className="kpi-card-icon" style={{ color: stat.color }} />
              </div>
              <div className="kpi-card-value" style={{ color: stat.color }}>
                {stat.value}
              </div>
              <div className="kpi-card-delta">{stat.delta}</div>
            </div>
          );
        })}
      </div>

      {/* 整定效果趋势图 */}
      <div className="card" style={{ marginBottom: '16px' }}>
        <div className="card-header">
          <h3>整定效果趋势（月度）</h3>
        </div>
        <div className="card-body">
          <div ref={trendChartRef} style={{ width: '100%', height: '260px' }} />
        </div>
      </div>

      {/* 整定记录表 */}
      <div className="card">
        <div className="card-header">
          <h3>整定记录</h3>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={TUNING_RECORDS}
            rowKey={(row) => row.id}
            onRowClick={handleRowClick}
            initialSortKey="time"
            initialSortDir="desc"
          />
        </div>
      </div>

      {/* 整定详情 Drawer */}
      <Drawer
        open={drawerOpen}
        title="整定详情"
        onClose={() => setDrawerOpen(false)}
        footer={
          <button type="button" className="btn btn-secondary" onClick={handleReview}>
            发起复评
          </button>
        }
      >
        {selectedRecord && (
          <div>
            {/* 基本信息 */}
            <div className="detail-meta-row" style={{ marginBottom: '16px' }}>
              <div className="detail-meta-item">
                <span className="label">回路名</span>
                <span className="value">{selectedRecord.loopName}</span>
              </div>
              <div className="detail-meta-item">
                <span className="label">整定时间</span>
                <span className="value mono">{selectedRecord.time}</span>
              </div>
              <div className="detail-meta-item">
                <span className="label">整定算法</span>
                <span className="value">
                  <span className="badge status-info badge-sm">{selectedRecord.algorithm}</span>
                </span>
              </div>
              <div className="detail-meta-item">
                <span className="label">操作人</span>
                <span className="value">{selectedRecord.operator}</span>
              </div>
            </div>

            {/* 评分对比 */}
            <div className="form-section" style={{ marginBottom: '16px' }}>
              <div className="form-section-header">
                <h3>评分对比</h3>
              </div>
              <div className="form-section-body">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
                  <div style={{ padding: '12px', border: '1px solid var(--border-default)', borderRadius: '8px', background: 'rgba(220, 53, 69, 0.04)', textAlign: 'center' }}>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>调整前评分</div>
                    <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: '#DC3545' }}>
                      {selectedRecord.scoreBefore}
                    </div>
                  </div>
                  <div style={{ padding: '12px', border: '1px solid var(--border-default)', borderRadius: '8px', background: 'rgba(25, 135, 84, 0.04)', textAlign: 'center' }}>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>调整后评分</div>
                    <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: '#198754' }}>
                      {selectedRecord.scoreAfter}
                    </div>
                  </div>
                  <div style={{ padding: '12px', border: '1px solid var(--border-default)', borderRadius: '8px', background: 'rgba(13, 110, 253, 0.04)', textAlign: 'center' }}>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>改善率</div>
                    <div className="mono" style={{ fontSize: '28px', fontWeight: 700, color: '#0D6EFD' }}>
                      +{selectedRecord.improvement}%
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* 整定说明 */}
            <div className="form-section">
              <div className="form-section-header">
                <h3>整定说明</h3>
              </div>
              <div className="form-section-body">
                <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  {selectedRecord.remark}
                </p>
              </div>
            </div>

            {/* 风险提示 */}
            <div style={{ marginTop: '16px', padding: '12px', background: 'rgba(255, 193, 7, 0.1)', border: '1px solid rgba(255, 193, 7, 0.3)', borderRadius: '8px', fontSize: '13px', color: '#B8860B' }}>
              <strong>风险说明：</strong>Phase 2 整定结果仅供参考，不下写 DCS。实际参数调整需由仪控工程师确认后手动下写。
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
