/**
 * 整定算法页面（Phase 2 §6.5.3）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.5.3
 *
 * 布局结构：
 * 1. 顶部：Phase 2 标签 + 回路选择 + 算法选择（Ziegler-Nichols / Cohen-Coon / IMC / Lambda）
 * 2. 中部：两列
 *    - 左侧：算法参数配置（form-section，如 IMC 的滤波因子、Lambda 的期望闭环时间常数）
 *    - 右侧：推荐 PID 参数卡片（P/I/D 推荐值 + 预期性能指标）
 * 3. 底部：多算法推荐对比表（DataTable），列：算法/推荐P/推荐I/推荐D/预期超调/预期响应时间
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 卡片用 border + radius-md，不用左 border accent
 */

import { useState, useMemo } from 'react';
import { FlaskConical, Calculator, SlidersHorizontal, Target } from 'lucide-react';
import { EmptyState } from '../../components/EmptyState';
import { DataTable, type Column } from '../../components/DataTable';
import { useToast } from '../../components/Toast';
import { loops, findLoop } from '../../mock/loops';

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

/** 整定算法选项 */
const ALGORITHMS = [
  { value: 'zn', label: 'Ziegler-Nichols' },
  { value: 'cc', label: 'Cohen-Coon' },
  { value: 'imc', label: 'IMC（内模控制）' },
  { value: 'lambda', label: 'Lambda' },
];

/** 算法参数配置项 */
interface AlgoParam {
  key: string;
  label: string;
  value: string;
  hint: string;
}

/** 推荐结果 */
interface AlgoRecommendation {
  p: string;
  i: string;
  d: string;
  overshoot: string;
  responseTime: string;
}

/** 多算法推荐对比表行 */
interface AlgoComparison {
  algorithm: string;
  p: string;
  i: string;
  d: string;
  overshoot: string;
  responseTime: string;
}

/** 多算法推荐对比表 mock 数据 */
const ALGO_COMPARISONS: AlgoComparison[] = [
  { algorithm: 'Ziegler-Nichols', p: '1.85', i: '8.2', d: '2.05', overshoot: '25%', responseTime: '15s' },
  { algorithm: 'Cohen-Coon', p: '1.72', i: '9.5', d: '1.85', overshoot: '20%', responseTime: '18s' },
  { algorithm: 'IMC（内模控制）', p: '1.45', i: '12.5', d: '1.20', overshoot: '8%', responseTime: '22s' },
  { algorithm: 'Lambda', p: '1.30', i: '15.0', d: '0.95', overshoot: '5%', responseTime: '28s' },
];

export default function TuningAlgorithmPage() {
  const toast = useToast();
  const [loopId, setLoopId] = useState(loops[0]?.loopId ?? '');
  const [algorithm, setAlgorithm] = useState('imc');
  const [imcFilter, setImcFilter] = useState('0.5');
  const [lambdaTime, setLambdaTime] = useState('15');

  const loop = useMemo(() => findLoop(loopId), [loopId]);

  /** 算法参数配置（根据算法类型动态生成） */
  const algoParams: AlgoParam[] = useMemo(() => {
    switch (algorithm) {
      case 'zn':
        return [
          { key: 'ku', label: '临界增益 Ku', value: '2.45', hint: '由闭环振荡测试自动获取' },
          { key: 'tu', label: '临界周期 Tu', value: '6.8s', hint: '由闭环振荡测试自动获取' },
        ];
      case 'cc':
        return [
          { key: 'k', label: '模型增益 K', value: '1.25', hint: '来自模型辨识结果' },
          { key: 't', label: '时间常数 T', value: '12.5s', hint: '来自模型辨识结果' },
          { key: 'tau', label: '纯滞后 τ', value: '3.2s', hint: '来自模型辨识结果' },
        ];
      case 'imc':
        return [
          { key: 'filter', label: '滤波因子 λ', value: imcFilter, hint: 'λ 越大越鲁棒，越小越快' },
          { key: 'k', label: '模型增益 K', value: '1.25', hint: '来自模型辨识结果' },
          { key: 't', label: '时间常数 T', value: '12.5s', hint: '来自模型辨识结果' },
        ];
      case 'lambda':
        return [
          { key: 'lambda', label: '期望闭环时间常数', value: lambdaTime, hint: '建议取 T ~ 3T 之间' },
          { key: 'k', label: '模型增益 K', value: '1.25', hint: '来自模型辨识结果' },
          { key: 't', label: '时间常数 T', value: '12.5s', hint: '来自模型辨识结果' },
        ];
      default:
        return [];
    }
  }, [algorithm, imcFilter, lambdaTime]);

  /** 推荐结果（根据算法生成 mock 数据） */
  const recommendation: AlgoRecommendation = useMemo(() => {
    const found = ALGO_COMPARISONS.find((a) => a.algorithm === ALGORITHMS.find((a) => a.value === algorithm)?.label);
    if (found) {
      return {
        p: found.p,
        i: found.i,
        d: found.d,
        overshoot: found.overshoot,
        responseTime: found.responseTime,
      };
    }
    return { p: '—', i: '—', d: '—', overshoot: '—', responseTime: '—' };
  }, [algorithm]);

  /** 多算法推荐对比表列定义 */
  const columns: Column<AlgoComparison>[] = [
    {
      key: 'algorithm',
      header: '算法',
      sortable: true,
      render: (row) => <strong>{row.algorithm}</strong>,
    },
    {
      key: 'p',
      header: '推荐 P',
      align: 'center',
      render: (row) => <span className="mono">{row.p}</span>,
    },
    {
      key: 'i',
      header: '推荐 I (s)',
      align: 'center',
      render: (row) => <span className="mono">{row.i}</span>,
    },
    {
      key: 'd',
      header: '推荐 D (s)',
      align: 'center',
      render: (row) => <span className="mono">{row.d}</span>,
    },
    {
      key: 'overshoot',
      header: '预期超调',
      sortable: true,
      align: 'center',
      render: (row) => {
        const val = parseFloat(row.overshoot);
        const color = val <= 10 ? '#198754' : val <= 20 ? '#FFC107' : '#DC3545';
        return (
          <span className="mono" style={{ color, fontWeight: 600 }}>
            {row.overshoot}
          </span>
        );
      },
    },
    {
      key: 'responseTime',
      header: '预期响应时间',
      sortable: true,
      align: 'center',
      render: (row) => <span className="mono">{row.responseTime}</span>,
    },
  ];

  /** 触发整定计算 */
  const handleCalculate = () => {
    toast.warning('Phase 2 功能，暂不可用');
  };

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            整定算法
            <Phase2Tag />
          </h1>
          <p className="page-subtitle">
            Phase 2 功能 · 支持 Ziegler-Nichols / Cohen-Coon / IMC / Lambda 四种整定算法
          </p>
        </div>
      </div>

      {/* 回路选择 + 算法选择 */}
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
          <div className="filter-item">
            <label>整定算法</label>
            <select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
              {ALGORITHMS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="filter-bar-actions">
          <button type="button" className="btn btn-primary" onClick={handleCalculate}>
            <Calculator size={14} />
            计算推荐参数
          </button>
        </div>
      </div>

      {/* Phase 2 原型提示 */}
      <EmptyState
        type="partial"
        title="Phase 2 功能，当前为原型演示"
        description="整定算法为 Phase 2 功能，当前页面仅展示 UI 原型与推荐参数展示形式，实际整定计算暂不可用。"
      />

      {/* 中部：两列 - 算法参数配置 + 推荐 PID 参数卡片 */}
      <div className="two-col-grid" style={{ marginTop: '16px' }}>
        {/* 左侧：算法参数配置 */}
        <div className="form-section" style={{ marginBottom: 0 }}>
          <div className="form-section-header">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <SlidersHorizontal size={16} />
              算法参数配置
            </h3>
          </div>
          <div className="form-section-body">
            {loop && (
              <div style={{ marginBottom: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
                回路：{loop.loopName} · 算法：{ALGORITHMS.find((a) => a.value === algorithm)?.label}
              </div>
            )}
            {algoParams.map((param) => (
              <div className="form-row" key={param.key}>
                <label>{param.label}</label>
                <div>
                  {param.key === 'filter' ? (
                    <input
                      type="number"
                      step="0.1"
                      value={param.value}
                      onChange={(e) => setImcFilter(e.target.value)}
                    />
                  ) : param.key === 'lambda' ? (
                    <input
                      type="number"
                      step="1"
                      value={param.value}
                      onChange={(e) => setLambdaTime(e.target.value)}
                    />
                  ) : (
                    <input type="text" value={param.value} readOnly style={{ background: 'var(--bg-muted)' }} />
                  )}
                  <div className="hint">{param.hint}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 右侧：推荐 PID 参数卡片 */}
        <div className="card">
          <div className="card-header">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Target size={16} />
              推荐 PID 参数
            </h3>
          </div>
          <div className="card-body">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginBottom: '16px' }}>
              <div style={{ padding: '12px', border: '1px solid var(--border-default)', borderRadius: '8px', background: 'var(--bg-muted)', textAlign: 'center' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>P（比例）</div>
                <div className="mono" style={{ fontSize: '24px', fontWeight: 700, color: 'var(--accent-blue)' }}>
                  {recommendation.p}
                </div>
              </div>
              <div style={{ padding: '12px', border: '1px solid var(--border-default)', borderRadius: '8px', background: 'var(--bg-muted)', textAlign: 'center' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>I (s)</div>
                <div className="mono" style={{ fontSize: '24px', fontWeight: 700, color: 'var(--accent-blue)' }}>
                  {recommendation.i}
                </div>
              </div>
              <div style={{ padding: '12px', border: '1px solid var(--border-default)', borderRadius: '8px', background: 'var(--bg-muted)', textAlign: 'center' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>D (s)</div>
                <div className="mono" style={{ fontSize: '24px', fontWeight: 700, color: 'var(--accent-blue)' }}>
                  {recommendation.d}
                </div>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              <div style={{ padding: '10px 12px', border: '1px solid var(--border-default)', borderRadius: '8px' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '2px' }}>预期超调</div>
                <div className="mono" style={{ fontSize: '18px', fontWeight: 600, color: '#198754' }}>
                  {recommendation.overshoot}
                </div>
              </div>
              <div style={{ padding: '10px 12px', border: '1px solid var(--border-default)', borderRadius: '8px' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '2px' }}>预期响应时间</div>
                <div className="mono" style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  {recommendation.responseTime}
                </div>
              </div>
            </div>
            <div style={{ marginTop: '12px', padding: '8px 12px', background: 'rgba(255, 193, 7, 0.1)', borderRadius: '4px', fontSize: '12px', color: '#B8860B' }}>
              <strong>安全提示：</strong>推荐参数仅供参考，需经仿真验证后方可下写 DCS（Phase 2 不支持下写）。
            </div>
          </div>
        </div>
      </div>

      {/* 底部：多算法推荐对比表 */}
      <div className="card" style={{ marginTop: '16px' }}>
        <div className="card-header">
          <h3>多算法推荐对比</h3>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={ALGO_COMPARISONS}
            rowKey={(row) => row.algorithm}
            initialSortKey="overshoot"
            initialSortDir="asc"
          />
        </div>
      </div>
    </div>
  );
}
