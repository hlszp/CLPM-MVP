/**
 * 波形分析详情页（v4.0 §6.4.2 + §6.4.3）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.4.2 + §6.4.3 + §7.3
 *
 * 布局结构（详情页）：
 * 1. detail-header：回路名 + 预诊标签 + 置信度 + 操作按钮（生成 Tracker / 返回列表）
 * 2. detail-meta-row：回路位号 / 所属节点 / PV 质量码 / 控制模式 / 评分 / 诊断时间
 * 3. Tabs：波形分析 / PV-OP 散点 / 诊断详情
 *    - Tab1 波形分析：WaveformChart + 质量码图例说明
 *    - Tab2 PV-OP 散点：ScatterChart + 粘滞阀特征说明
 *    - Tab3 诊断详情：诊断结果详情（预诊标签、置信度、详情描述、建议措施）
 *
 * 从 URL 参数 ?loopId=xxx 获取回路 ID
 */

import { useState, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, FileText, Activity, ScatterChart as ScatterIcon, ClipboardList } from 'lucide-react';
import { WaveformChart } from '../../components/WaveformChart';
import { ScatterChart } from '../../components/ScatterChart';
import { PVQualityBadge } from '../../components/PVQualityBadge';
import { ControlModeBadge, DiagnosisLabelBadge, ScoreBadge } from '../../components/StatusBadge';
import { EmptyState } from '../../components/EmptyState';
import { useToast } from '../../components/Toast';
import { findLoop } from '../../mock/loops';
import { findDiagnosisByLoop } from '../../mock/diagnosis';
import { findTrackerByLoop } from '../../mock/tracker';
import { getTimeseries, getScatterData } from '../../mock/timeseries';

type TabKey = 'waveform' | 'scatter' | 'detail';

export default function WaveformPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const toast = useToast();

  const loopId = searchParams.get('loopId') ?? '';
  const [activeTab, setActiveTab] = useState<TabKey>('waveform');

  // 获取回路、诊断、时序数据
  const loop = useMemo(() => findLoop(loopId), [loopId]);
  const diagnosis = useMemo(() => findDiagnosisByLoop(loopId), [loopId]);
  const dataset = useMemo(() => (loopId ? getTimeseries(loopId) : null), [loopId]);
  const scatterData = useMemo(() => (loopId ? getScatterData(loopId) : []), [loopId]);
  const existingTracker = useMemo(() => findTrackerByLoop(loopId), [loopId]);

  // 回路不存在时显示空状态
  if (!loop) {
    return (
      <div className="page-container">
        <div className="page-empty-state">
          <EmptyState
            type="empty"
            title="回路不存在"
            description={`未找到 loopId=${loopId} 的回路，请从诊断列表进入`}
            action={
              <button type="button" className="btn btn-primary" onClick={() => navigate('/diagnosis')}>
                <ArrowLeft size={14} />
                返回诊断列表
              </button>
            }
          />
        </div>
      </div>
    );
  }

  // 生成 Tracker 操作
  const handleGenerateTracker = () => {
    if (existingTracker) {
      toast.warning(`该回路已存在 Tracker ${existingTracker.trackerId}，请在异常跟踪页查看`);
      return;
    }
    if (!diagnosis) {
      toast.error('未找到诊断结果，无法生成 Tracker');
      return;
    }
    toast.success(`已为回路 ${loop.loopName} 生成 Action Tracker，请在异常跟踪页处理`);
  };

  // Tab 配置
  const tabs: Array<{ key: TabKey; label: string; icon: typeof Activity }> = [
    { key: 'waveform', label: '波形分析', icon: Activity },
    { key: 'scatter', label: 'PV-OP 散点', icon: ScatterIcon },
    { key: 'detail', label: '诊断详情', icon: ClipboardList },
  ];

  return (
    <div className="page-container">
      {/* 详情头部 */}
      <div className="detail-header">
        <div className="detail-title-block">
          <h1>
            {loop.loopName}
            {diagnosis && <DiagnosisLabelBadge label={diagnosis.label} size="md" />}
          </h1>
          <div className="subtitle">
            {loop.loopCode} · 置信度 {diagnosis ? Math.round(diagnosis.confidence * 100) : '—'}%
          </div>
        </div>
        <div className="detail-actions">
          <button type="button" className="btn btn-secondary" onClick={() => navigate('/diagnosis')}>
            <ArrowLeft size={14} />
            返回列表
          </button>
          <button type="button" className="btn btn-primary" onClick={handleGenerateTracker}>
            <FileText size={14} />
            {existingTracker ? '查看 Tracker' : '生成 Tracker'}
          </button>
        </div>
      </div>

      {/* 元信息行 */}
      <div className="detail-meta-row">
        <div className="detail-meta-item">
          <span className="label">回路位号</span>
          <span className="value mono">{loop.loopCode}</span>
        </div>
        <div className="detail-meta-item">
          <span className="label">所属节点</span>
          <span className="value">{loop.nodeName}</span>
        </div>
        <div className="detail-meta-item">
          <span className="label">PV 质量码</span>
          <PVQualityBadge quality={loop.pvQuality} />
        </div>
        <div className="detail-meta-item">
          <span className="label">控制模式</span>
          <ControlModeBadge mode={loop.controlMode} />
        </div>
        <div className="detail-meta-item">
          <span className="label">综合评分</span>
          <ScoreBadge score={loop.score} />
        </div>
        <div className="detail-meta-item">
          <span className="label">诊断时间</span>
          <span className="value mono">{diagnosis?.diagnosisTime ?? '—'}</span>
        </div>
      </div>

      {/* 标签页 */}
      <div className="tabs">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.key}
              type="button"
              className={`tab ${activeTab === tab.key ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.key)}
            >
              <Icon size={14} style={{ marginRight: 'var(--space-1)', verticalAlign: 'middle' }} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab 内容 */}
      {activeTab === 'waveform' && dataset && (
        <div className="card">
          <div className="card-header">
            <h3>30 天时序波形（PV 按质量码分段渲染）</h3>
            <span className="mono" style={{ fontSize: 'var(--text-small)', color: 'var(--text-muted)' }}>
              采样点 {dataset.sampleCount}
            </span>
          </div>
          <div className="card-body">
            <WaveformChart dataset={dataset} height="360px" showDataZoom={true} showOp={true} />
            {/* 质量码图例说明 */}
            <div style={{ display: 'flex', gap: 'var(--space-4)', marginTop: 'var(--space-3)', fontSize: 'var(--text-small)', color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}>
                <span style={{ display: 'inline-block', width: 16, height: 2, background: '#198754' }} />
                Good 实线（PV 正常）
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}>
                <span style={{ display: 'inline-block', width: 16, height: 8, background: 'rgba(220, 53, 69, 0.08)', border: '1px solid rgba(220, 53, 69, 0.3)' }} />
                Bad 灰色断线 + 红色背景（PV 不可信）
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}>
                <span style={{ display: 'inline-block', width: 16, height: 2, background: '#FFC107', borderTop: '2px dashed #FFC107' }} />
                Uncertain 琥珀虚线（PV 不确定）
              </span>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'scatter' && (
        <div className="card">
          <div className="card-header">
            <h3>PV-OP 散点图（最近 500 个 Good 质量点）</h3>
          </div>
          <div className="card-body">
            <ScatterChart data={scatterData} height="320px" />
            {/* 粘滞阀特征说明 */}
            <div style={{ marginTop: 'var(--space-3)', padding: 'var(--space-3)', background: 'var(--bg-muted)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
              <strong style={{ color: 'var(--text-primary)' }}>粘滞阀特征说明：</strong>
              散点呈"鱼骨"或"双带"分布时，OP 持续变化但 PV 响应迟滞，表明阀门存在静摩擦导致的粘滞现象。
              正常控制回路散点应呈紧凑椭圆分布。
              {diagnosis?.label === '粘滞阀' && (
                <span style={{ color: 'var(--status-warning)', marginLeft: 'var(--space-2)' }}>
                  当前回路检测到粘滞阀特征，建议安排阀门检修。
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'detail' && diagnosis && (
        <div className="card">
          <div className="card-header">
            <h3>诊断结果详情</h3>
          </div>
          <div className="card-body">
            <div className="form-row">
              <label>预诊标签</label>
              <div><DiagnosisLabelBadge label={diagnosis.label} size="md" /></div>
            </div>
            <div className="form-row">
              <label>置信度</label>
              <div className="mono" style={{ fontWeight: 600 }}>
                {Math.round(diagnosis.confidence * 100)}%
              </div>
            </div>
            <div className="form-row">
              <label>诊断详情</label>
              <div style={{ lineHeight: 1.6, color: 'var(--text-primary)' }}>{diagnosis.detail}</div>
            </div>
            <div className="form-row">
              <label>建议措施</label>
              <div style={{ lineHeight: 1.6, color: 'var(--text-primary)' }}>{diagnosis.suggestion}</div>
            </div>
            <div className="form-row">
              <label>诊断时间</label>
              <div className="mono">{diagnosis.diagnosisTime}</div>
            </div>
            <div className="form-row">
              <label>Tracker 状态</label>
              <div>
                {existingTracker ? (
                  <span className="badge status-success badge-sm">
                    已生成 {existingTracker.trackerId}
                  </span>
                ) : (
                  <span className="badge status-neutral badge-sm">未生成</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'detail' && !diagnosis && (
        <div className="page-empty-state">
          <EmptyState type="empty" title="无诊断结果" description="该回路暂未生成诊断结果" />
        </div>
      )}
    </div>
  );
}
