/**
 * 闭环仿真页面（Phase 2 §6.5.4）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.5.4
 *
 * 布局结构：
 * 1. 顶部：Phase 2 标签 + 回路选择
 * 2. 中部：仿真参数配置（form-section）
 *    - 仿真时长、步长、设定值阶跃幅度、扰动幅度、扰动时间
 *    - 当前 PID 参数 vs 推荐参数对比输入
 * 3. 底部：仿真结果波形（WaveformChart，双波形对比：当前参数 vs 推荐参数）
 *    - 波形下方：性能指标对比表（超调量/响应时间/IAE/上升时间）
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 卡片用 border + radius-md，不用左 border accent
 */

import { useState, useMemo, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { FlaskConical, Play, Settings2, BarChart3, GitCompare } from 'lucide-react';
import { EmptyState } from '../../components/EmptyState';
import { DataTable, type Column } from '../../components/DataTable';
import { useToast } from '../../components/Toast';
import { loops, findLoop } from '../../mock/loops';
import { getTimeseries } from '../../mock/timeseries';

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

/** 性能指标对比表行 */
interface PerfMetric {
  metric: string;
  current: string;
  recommended: string;
  improvement: string;
}

/** 性能指标对比表 mock 数据 */
const PERF_METRICS: PerfMetric[] = [
  { metric: '超调量', current: '25%', recommended: '8%', improvement: '↓ 68%' },
  { metric: '响应时间', current: '15s', recommended: '22s', improvement: '↑ 47%' },
  { metric: 'IAE（绝对误差积分）', current: '185.3', recommended: '98.6', improvement: '↓ 47%' },
  { metric: '上升时间', current: '6.5s', recommended: '9.2s', improvement: '↑ 42%' },
];

export default function SimulationPage() {
  const toast = useToast();
  const [loopId, setLoopId] = useState(loops[0]?.loopId ?? '');
  const [duration, setDuration] = useState('120');
  const [step, setStep] = useState('0.1');
  const [stepAmplitude, setStepAmplitude] = useState('10');
  const [disturbAmplitude, setDisturbAmplitude] = useState('5');
  const [disturbTime, setDisturbTime] = useState('60');
  const [currentP, setCurrentP] = useState('1.20');
  const [currentI, setCurrentI] = useState('0.50');
  const [currentD, setCurrentD] = useState('0.10');
  const [recommendedP, setRecommendedP] = useState('1.45');
  const [recommendedI, setRecommendedI] = useState('12.5');
  const [recommendedD, setRecommendedD] = useState('1.20');

  const loop = useMemo(() => findLoop(loopId), [loopId]);
  const dataset = useMemo(() => getTimeseries(loopId), [loopId]);

  /** 双波形对比图 ref */
  const compareChartRef = useRef<HTMLDivElement>(null);
  const compareChartInstance = useRef<echarts.ECharts | null>(null);

  /** 构造双波形对比数据（当前参数 vs 推荐参数） */
  const compareData = useMemo(() => {
    const points = dataset.points;
    // 取前 200 个点用于仿真对比展示
    const sample = points.slice(0, 200);
    const currentSeries: Array<[number, number]> = [];
    const recommendedSeries: Array<[number, number]> = [];
    const spSeries: Array<[number, number]> = [];

    // 确定性伪噪声函数（避免在 useMemo 中调用 Math.random 等不纯函数）
    const deterministicNoise = (seed: number, amplitude: number) => {
      const x = Math.sin(seed * 12.9898) * 43758.5453;
      return (x - Math.floor(x) - 0.5) * amplitude;
    };

    sample.forEach((p, idx) => {
      const ts = p.timestamp;
      const baseSp = p.sp ?? 50;
      spSeries.push([ts, baseSp]);
      // 当前参数：振荡较大
      const currentVal = baseSp + Math.sin(idx * 0.3) * 3.5 + deterministicNoise(idx, 0.8);
      currentSeries.push([ts, currentVal]);
      // 推荐参数：更平稳
      const recommendedVal = baseSp + Math.sin(idx * 0.1) * 0.8 * Math.exp(-idx * 0.02) + deterministicNoise(idx + 100, 0.3);
      recommendedSeries.push([ts, recommendedVal]);
    });

    return { currentSeries, recommendedSeries, spSeries };
  }, [dataset]);

  /** 渲染双波形对比图 */
  useEffect(() => {
    if (!compareChartRef.current) return;
    if (!compareChartInstance.current) {
      compareChartInstance.current = echarts.init(compareChartRef.current);
    }

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#6C757D' } },
      },
      legend: { top: 0, right: 10, textStyle: { fontSize: 12 } },
      grid: { left: 50, right: 20, top: 35, bottom: 30 },
      xAxis: {
        type: 'time',
        axisLabel: { fontSize: 11, color: '#6C757D' },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        name: 'PV/SP',
        nameTextStyle: { fontSize: 11, color: '#6C757D' },
        axisLabel: { fontSize: 11, color: '#6C757D' },
        splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      },
      series: [
        {
          name: 'SP（设定值）',
          type: 'line',
          data: compareData.spSeries,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#0D6EFD', width: 1.2, type: 'dashed' },
          connectNulls: true,
        },
        {
          name: '当前参数响应',
          type: 'line',
          data: compareData.currentSeries,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#DC3545', width: 1.5 },
          connectNulls: true,
        },
        {
          name: '推荐参数响应',
          type: 'line',
          data: compareData.recommendedSeries,
          smooth: false,
          symbol: 'none',
          lineStyle: { color: '#198754', width: 1.5 },
          connectNulls: true,
        },
      ],
    };

    compareChartInstance.current.setOption(option, true);
  }, [compareData]);

  /** 响应式调整 */
  useEffect(() => {
    const handleResize = () => compareChartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      compareChartInstance.current?.dispose();
      compareChartInstance.current = null;
    };
  }, []);

  /** 性能指标对比表列定义 */
  const columns: Column<PerfMetric>[] = [
    {
      key: 'metric',
      header: '性能指标',
      render: (row) => <strong>{row.metric}</strong>,
    },
    {
      key: 'current',
      header: '当前参数',
      align: 'center',
      render: (row) => <span className="mono" style={{ color: '#DC3545' }}>{row.current}</span>,
    },
    {
      key: 'recommended',
      header: '推荐参数',
      align: 'center',
      render: (row) => <span className="mono" style={{ color: '#198754' }}>{row.recommended}</span>,
    },
    {
      key: 'improvement',
      header: '改善幅度',
      align: 'center',
      render: (row) => {
        const isImprovement = row.improvement.startsWith('↓');
        const color = isImprovement ? '#198754' : '#FFC107';
        return (
          <span className="mono" style={{ color, fontWeight: 600 }}>
            {row.improvement}
          </span>
        );
      },
    },
  ];

  /** 触发仿真 */
  const handleSimulate = () => {
    toast.warning('Phase 2 功能，暂不可用');
  };

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            闭环仿真
            <Phase2Tag />
          </h1>
          <p className="page-subtitle">
            Phase 2 功能 · 当前 PID 参数 vs 推荐 PID 参数 双波形对比仿真
          </p>
        </div>
      </div>

      {/* 回路选择 */}
      <div className="filter-bar">
        <div className="filter-bar-left">
          <div className="filter-item">
            <label>选择回路</label>
            <select value={loopId} onChange={(e) => setLoopId(e.target.value)}>
              {loops.map((l) => (
                <option key={l.loopId} value={l.loopId}>
                  {l.loopName}（{l.loopCode}）
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="filter-bar-actions">
          <button type="button" className="btn btn-primary" onClick={handleSimulate}>
            <Play size={14} />
            开始仿真
          </button>
        </div>
      </div>

      {/* Phase 2 原型提示 */}
      <EmptyState
        type="partial"
        title="Phase 2 功能，当前为原型演示"
        description="闭环仿真为 Phase 2 功能，当前页面仅展示 UI 原型与对比波形展示形式，实际仿真引擎暂不可用。"
      />

      {/* 仿真参数配置 */}
      <div className="form-section" style={{ marginTop: '16px' }}>
        <div className="form-section-header">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Settings2 size={16} />
            仿真参数配置
          </h3>
        </div>
        <div className="form-section-body">
          {loop && (
            <div style={{ marginBottom: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
              回路：{loop.loopName} · 当前模式：{loop.controlMode}
            </div>
          )}
          {/* 仿真参数 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px 24px', marginBottom: '16px' }}>
            <div className="form-row" style={{ marginBottom: 0 }}>
              <label>仿真时长 (s)</label>
              <input type="number" value={duration} onChange={(e) => setDuration(e.target.value)} />
            </div>
            <div className="form-row" style={{ marginBottom: 0 }}>
              <label>仿真步长 (s)</label>
              <input type="number" step="0.01" value={step} onChange={(e) => setStep(e.target.value)} />
            </div>
            <div className="form-row" style={{ marginBottom: 0 }}>
              <label>设定值阶跃幅度</label>
              <input type="number" value={stepAmplitude} onChange={(e) => setStepAmplitude(e.target.value)} />
            </div>
            <div className="form-row" style={{ marginBottom: 0 }}>
              <label>扰动幅度</label>
              <input type="number" value={disturbAmplitude} onChange={(e) => setDisturbAmplitude(e.target.value)} />
            </div>
            <div className="form-row" style={{ marginBottom: 0 }}>
              <label>扰动时间 (s)</label>
              <input type="number" value={disturbTime} onChange={(e) => setDisturbTime(e.target.value)} />
            </div>
          </div>

          {/* 当前 PID vs 推荐 PID 对比 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border-default)' }}>
            <div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#DC3545', marginBottom: '8px' }}>当前 PID 参数</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
                <div className="form-row" style={{ marginBottom: 0, gridTemplateColumns: '1fr' }}>
                  <label>P</label>
                  <input type="number" step="0.01" value={currentP} onChange={(e) => setCurrentP(e.target.value)} />
                </div>
                <div className="form-row" style={{ marginBottom: 0, gridTemplateColumns: '1fr' }}>
                  <label>I (s)</label>
                  <input type="number" step="0.01" value={currentI} onChange={(e) => setCurrentI(e.target.value)} />
                </div>
                <div className="form-row" style={{ marginBottom: 0, gridTemplateColumns: '1fr' }}>
                  <label>D (s)</label>
                  <input type="number" step="0.01" value={currentD} onChange={(e) => setCurrentD(e.target.value)} />
                </div>
              </div>
            </div>
            <div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#198754', marginBottom: '8px' }}>推荐 PID 参数</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
                <div className="form-row" style={{ marginBottom: 0, gridTemplateColumns: '1fr' }}>
                  <label>P</label>
                  <input type="number" step="0.01" value={recommendedP} onChange={(e) => setRecommendedP(e.target.value)} />
                </div>
                <div className="form-row" style={{ marginBottom: 0, gridTemplateColumns: '1fr' }}>
                  <label>I (s)</label>
                  <input type="number" step="0.01" value={recommendedI} onChange={(e) => setRecommendedI(e.target.value)} />
                </div>
                <div className="form-row" style={{ marginBottom: 0, gridTemplateColumns: '1fr' }}>
                  <label>D (s)</label>
                  <input type="number" step="0.01" value={recommendedD} onChange={(e) => setRecommendedD(e.target.value)} />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 仿真结果波形 */}
      <div className="card" style={{ marginTop: '16px' }}>
        <div className="card-header">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BarChart3 size={16} />
            仿真结果波形（当前参数 vs 推荐参数）
          </h3>
        </div>
        <div className="card-body">
          <div ref={compareChartRef} style={{ width: '100%', height: '320px' }} />
          <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
            红色：当前参数响应 · 绿色：推荐参数响应 · 蓝色虚线：设定值
          </div>
        </div>
      </div>

      {/* 性能指标对比表 */}
      <div className="card" style={{ marginTop: '16px' }}>
        <div className="card-header">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <GitCompare size={16} />
            性能指标对比
          </h3>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={PERF_METRICS}
            rowKey={(row) => row.metric}
          />
        </div>
      </div>
    </div>
  );
}
