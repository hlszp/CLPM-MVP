/**
 * 模型辨识页面（Phase 2 §6.5.2）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.5.2
 *
 * 布局结构：
 * 1. 顶部：Phase 2 标签 + 回路选择 + 辨识方法下拉（阶跃响应/伪随机信号/继电反馈）
 * 2. 中部：辨识输入信号波形 + 辨识结果模型参数卡片
 *    - 模型参数：增益 K / 时间常数 T / 纯滞后 τ / 模型阶次 / 拟合度 R²
 * 3. 底部：辨识结果对比表（DataTable），列：模型类型/参数/拟合度/适用场景
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 卡片用 border + radius-md，不用左 border accent
 */

import { useState, useMemo } from 'react';
import { FlaskConical, Activity, BarChart3, GitCompare } from 'lucide-react';
import { WaveformChart } from '../../components/WaveformChart';
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

/** 辨识方法选项 */
const IDENTIFY_METHODS = [
  { value: 'step', label: '阶跃响应法' },
  { value: 'prbs', label: '伪随机信号法' },
  { value: 'relay', label: '继电反馈法' },
];

/** 模型参数项 */
interface ModelParam {
  key: string;
  label: string;
  value: string;
  unit: string;
}

/** 辨识结果对比表行 */
interface IdentifyResult {
  modelType: string;
  params: string;
  fitness: number;
  scenario: string;
}

/** 模型辨识结果对比表 mock 数据 */
const IDENTIFY_RESULTS: IdentifyResult[] = [
  {
    modelType: 'FOPDT（一阶加纯滞后）',
    params: 'K=1.25, T=12.5s, τ=3.2s',
    fitness: 0.92,
    scenario: '常规温度/流量回路，模型简单，适用于 Z-N / Cohen-Coon 整定',
  },
  {
    modelType: 'SOPDT（二阶加纯滞后）',
    params: 'K=1.20, T1=8.5s, T2=4.2s, τ=2.1s',
    fitness: 0.96,
    scenario: '高精度场景，适用于 IMC / Lambda 整定，拟合度更高',
  },
  {
    modelType: 'IPDT（积分加纯滞后）',
    params: 'Kv=0.08, τ=5.5s',
    fitness: 0.85,
    scenario: '液位类积分回路，适用于液位控制整定',
  },
];

export default function ModelIdentifyPage() {
  const toast = useToast();
  const [loopId, setLoopId] = useState(loops[0]?.loopId ?? '');
  const [method, setMethod] = useState('step');

  const loop = useMemo(() => findLoop(loopId), [loopId]);
  const dataset = useMemo(() => getTimeseries(loopId), [loopId]);

  /** 模型参数（根据辨识方法生成 mock 数据） */
  const modelParams: ModelParam[] = useMemo(() => {
    const baseParams: Record<string, ModelParam[]> = {
      step: [
        { key: 'K', label: '增益 K', value: '1.25', unit: '—' },
        { key: 'T', label: '时间常数 T', value: '12.5', unit: 's' },
        { key: 'tau', label: '纯滞后 τ', value: '3.2', unit: 's' },
        { key: 'order', label: '模型阶次', value: '1', unit: '阶' },
        { key: 'r2', label: '拟合度 R²', value: '0.92', unit: '—' },
      ],
      prbs: [
        { key: 'K', label: '增益 K', value: '1.18', unit: '—' },
        { key: 'T', label: '时间常数 T', value: '11.8', unit: 's' },
        { key: 'tau', label: '纯滞后 τ', value: '2.8', unit: 's' },
        { key: 'order', label: '模型阶次', value: '2', unit: '阶' },
        { key: 'r2', label: '拟合度 R²', value: '0.96', unit: '—' },
      ],
      relay: [
        { key: 'K', label: '增益 K', value: '1.22', unit: '—' },
        { key: 'T', label: '时间常数 T', value: '13.0', unit: 's' },
        { key: 'tau', label: '纯滞后 τ', value: '3.5', unit: 's' },
        { key: 'order', label: '模型阶次', value: '1', unit: '阶' },
        { key: 'r2', label: '拟合度 R²', value: '0.88', unit: '—' },
      ],
    };
    return baseParams[method] ?? baseParams.step;
  }, [method]);

  /** 辨识结果对比表列定义 */
  const columns: Column<IdentifyResult>[] = [
    {
      key: 'modelType',
      header: '模型类型',
      sortable: true,
      render: (row) => <strong>{row.modelType}</strong>,
    },
    {
      key: 'params',
      header: '参数',
      render: (row) => <span className="mono">{row.params}</span>,
    },
    {
      key: 'fitness',
      header: '拟合度 R²',
      sortable: true,
      align: 'center',
      render: (row) => {
        const color = row.fitness >= 0.95 ? '#198754' : row.fitness >= 0.9 ? '#FFC107' : '#DC3545';
        return (
          <span className="mono" style={{ color, fontWeight: 600 }}>
            {row.fitness.toFixed(2)}
          </span>
        );
      },
    },
    {
      key: 'scenario',
      header: '适用场景',
      render: (row) => <span style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>{row.scenario}</span>,
    },
  ];

  /** 触发辨识 */
  const handleIdentify = () => {
    toast.warning('Phase 2 功能，暂不可用');
  };

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            模型辨识
            <Phase2Tag />
          </h1>
          <p className="page-subtitle">
            Phase 2 功能 · 通过阶跃响应/伪随机信号/继电反馈辨识被控对象传递函数模型
          </p>
        </div>
      </div>

      {/* 回路选择 + 辨识方法 */}
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
            <label>辨识方法</label>
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              {IDENTIFY_METHODS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="filter-bar-actions">
          <button type="button" className="btn btn-primary" onClick={handleIdentify}>
            <Activity size={14} />
            开始辨识
          </button>
        </div>
      </div>

      {/* Phase 2 原型提示 */}
      <EmptyState
        type="partial"
        title="Phase 2 功能，当前为原型演示"
        description="模型辨识为 Phase 2 功能，当前页面仅展示 UI 原型与辨识结果展示形式，实际辨识算法暂不可用。"
      />

      {/* 中部：辨识输入信号波形 + 模型参数卡片 */}
      <div className="two-col-grid" style={{ marginTop: '16px' }}>
        {/* 辨识输入信号波形 */}
        <div className="card">
          <div className="card-header">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <BarChart3 size={16} />
              辨识输入信号波形
            </h3>
          </div>
          <div className="card-body">
            <WaveformChart dataset={dataset} height="280px" showDataZoom={false} />
            <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
              当前展示回路历史波形（Phase 2 将替换为辨识激励信号波形）
            </div>
          </div>
        </div>

        {/* 辨识结果模型参数卡片 */}
        <div className="card">
          <div className="card-header">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <GitCompare size={16} />
              辨识结果模型参数
            </h3>
          </div>
          <div className="card-body">
            {loop && (
              <div style={{ marginBottom: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
                回路：{loop.loopName} · 方法：{IDENTIFY_METHODS.find((m) => m.value === method)?.label}
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              {modelParams.map((p) => (
                <div
                  key={p.key}
                  style={{
                    padding: '12px',
                    border: '1px solid var(--border-default)',
                    borderRadius: '8px',
                    background: 'var(--bg-muted)',
                  }}
                >
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                    {p.label}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
                    <span
                      className="mono"
                      style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)' }}
                    >
                      {p.value}
                    </span>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{p.unit}</span>
                  </div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: '12px', padding: '8px 12px', background: 'rgba(13, 110, 253, 0.06)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
              <strong>传递函数：</strong>
              <span className="mono">G(s) = K · e^(-τs) / (Ts + 1)</span>
            </div>
          </div>
        </div>
      </div>

      {/* 底部：辨识结果对比表 */}
      <div className="card" style={{ marginTop: '16px' }}>
        <div className="card-header">
          <h3>辨识结果对比</h3>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={IDENTIFY_RESULTS}
            rowKey={(row) => row.modelType}
            initialSortKey="fitness"
            initialSortDir="desc"
          />
        </div>
      </div>
    </div>
  );
}
