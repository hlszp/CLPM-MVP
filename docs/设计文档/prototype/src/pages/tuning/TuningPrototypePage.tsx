/**
 * 整定原型页面（Phase 2 §6.5.1）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.5.1
 *
 * 布局结构：
 * 1. 顶部：Phase 2 标签 + 回路选择下拉框
 * 2. 左侧：当前 PID 参数面板（P/I/D 值，可编辑输入框）
 * 3. 右侧：实时仿真波形（WaveformChart，展示当前参数下的阶跃响应）
 * 4. 底部：操作按钮（开始仿真 / 重置参数 / 保存方案）
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 卡片用 border + radius-md，不用左 border accent
 */

import { useState, useMemo } from 'react';
import { Play, RotateCcw, Save, SlidersHorizontal, FlaskConical } from 'lucide-react';
import { WaveformChart } from '../../components/WaveformChart';
import { EmptyState } from '../../components/EmptyState';
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

export default function TuningPrototypePage() {
  const toast = useToast();
  const [loopId, setLoopId] = useState(loops[0]?.loopId ?? '');
  const [pidP, setPidP] = useState('1.20');
  const [pidI, setPidI] = useState('0.50');
  const [pidD, setPidD] = useState('0.10');

  const loop = useMemo(() => findLoop(loopId), [loopId]);
  const dataset = useMemo(() => getTimeseries(loopId), [loopId]);

  /** 重置参数到默认值 */
  const handleReset = () => {
    setPidP('1.20');
    setPidI('0.50');
    setPidD('0.10');
    toast.warning('Phase 2 功能，暂不可用');
  };

  /** 开始仿真 */
  const handleStartSim = () => {
    toast.warning('Phase 2 功能，暂不可用');
  };

  /** 保存方案 */
  const handleSave = () => {
    toast.warning('Phase 2 功能，暂不可用');
  };

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            整定工作台原型
            <Phase2Tag />
          </h1>
          <p className="page-subtitle">
            Phase 2 功能 · 当前为原型演示 · 不下写 DCS
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
      </div>

      {/* Phase 2 原型提示 */}
      <EmptyState
        type="partial"
        title="Phase 2 功能，当前为原型演示"
        description="整定工作台为 Phase 2 功能，当前页面仅展示 UI 原型与交互流程，仿真与参数下写功能暂不可用。"
      />

      {/* 两列：PID 参数 + 仿真波形 */}
      <div className="two-col-grid" style={{ marginTop: '16px' }}>
        {/* 左侧：当前 PID 参数面板 */}
        <div className="card">
          <div className="card-header">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <SlidersHorizontal size={16} />
              当前 PID 参数
            </h3>
          </div>
          <div className="card-body">
            {loop && (
              <div style={{ marginBottom: '16px', fontSize: '12px', color: 'var(--text-muted)' }}>
                <div>回路：{loop.loopName}</div>
                <div>位号：{loop.loopCode}</div>
                <div>装置：{loop.nodeName}</div>
                <div>当前模式：{loop.controlMode}</div>
              </div>
            )}
            <div className="form-section-body" style={{ padding: 0 }}>
              <div className="form-row">
                <label>P（比例增益）</label>
                <input
                  type="number"
                  step="0.01"
                  value={pidP}
                  onChange={(e) => setPidP(e.target.value)}
                />
              </div>
              <div className="form-row">
                <label>I（积分时间 s）</label>
                <input
                  type="number"
                  step="0.01"
                  value={pidI}
                  onChange={(e) => setPidI(e.target.value)}
                />
              </div>
              <div className="form-row">
                <label>D（微分时间 s）</label>
                <input
                  type="number"
                  step="0.01"
                  value={pidD}
                  onChange={(e) => setPidD(e.target.value)}
                />
              </div>
            </div>
            <div style={{ marginTop: '12px', padding: '8px 12px', background: 'var(--bg-muted)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
              <strong>提示：</strong>修改 PID 参数后点击"开始仿真"查看阶跃响应波形（Phase 2 功能）。
            </div>
          </div>
        </div>

        {/* 右侧：实时仿真波形 */}
        <div className="card">
          <div className="card-header">
            <h3>实时仿真波形（阶跃响应）</h3>
          </div>
          <div className="card-body">
            <WaveformChart dataset={dataset} height="320px" showDataZoom={false} />
            <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
              当前展示回路历史波形（Phase 2 将替换为基于 PID 参数的实时仿真波形）
            </div>
          </div>
        </div>
      </div>

      {/* 底部操作按钮 */}
      <div className="form-section">
        <div className="form-actions">
          <button type="button" className="btn btn-secondary" onClick={handleReset}>
            <RotateCcw size={14} />
            重置参数
          </button>
          <button type="button" className="btn btn-secondary" onClick={handleSave}>
            <Save size={14} />
            保存方案
          </button>
          <button type="button" className="btn btn-primary" onClick={handleStartSim}>
            <Play size={14} />
            开始仿真
          </button>
        </div>
      </div>
    </div>
  );
}
