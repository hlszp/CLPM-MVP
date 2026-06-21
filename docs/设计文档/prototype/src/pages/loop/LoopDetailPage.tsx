/**
 * 回路运行详情页（v4.0 §6.2.5）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.2.5
 *
 * 布局结构：
 * 1. 详情头部：回路名/位号/所属节点 + 返回按钮 + 控制模式/PV质量徽章
 * 2. Tag 关联信息（7 槽位）+ 当前 PV/SP/OP 值
 * 3. PV/SP/OP 时序波形（PV 按质量码分段渲染）
 * 4. KPI 摘要（6 项指标分项得分）
 *
 * 设计 grammar：Lucide 图标 / 工业配色 / border + radius-md / 状态色驱动
 */

import { useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Tag, Activity, Gauge } from 'lucide-react';
import { WaveformChart } from '../../components/WaveformChart';
import { PVQualityBadge } from '../../components/PVQualityBadge';
import {
  ComputeStatusBadge,
  ControlModeBadge,
  ScoreBadge,
} from '../../components/StatusBadge';
import { EmptyState } from '../../components/EmptyState';
import { findLoop } from '../../mock/loops';
import { findTagsByLoop } from '../../mock/aasTags';
import { getTimeseries } from '../../mock/timeseries';
import { kpiSnapshots, kpiDefinitions } from '../../mock/kpi';
import { findDiagnosisByLoop } from '../../mock/diagnosis';
import type { TagSlotKey } from '../../mock/types';

/** 7 槽位定义 */
const TAG_SLOTS: Array<{ key: TagSlotKey; label: string; required: boolean }> = [
  { key: 'PV', label: 'PV（过程变量）', required: true },
  { key: 'SP', label: 'SP（设定值）', required: true },
  { key: 'OP', label: 'OP（操作输出）', required: true },
  { key: 'MODE', label: 'MODE（控制模式）', required: true },
  { key: 'PID_P', label: 'PID_P', required: false },
  { key: 'PID_I', label: 'PID_I', required: false },
  { key: 'PID_D', label: 'PID_D', required: false },
];

export function LoopDetailPage() {
  const { loopId } = useParams<{ loopId: string }>();
  const navigate = useNavigate();

  const loop = useMemo(() => (loopId ? findLoop(loopId) : undefined), [loopId]);
  const tags = useMemo(() => (loopId ? findTagsByLoop(loopId) : []), [loopId]);
  const dataset = useMemo(() => (loopId ? getTimeseries(loopId) : null), [loopId]);
  const snapshot = useMemo(
    () => kpiSnapshots.find((s) => s.loopId === loopId),
    [loopId],
  );
  const diagnosis = useMemo(
    () => (loopId ? findDiagnosisByLoop(loopId) : undefined),
    [loopId],
  );

  if (!loop) {
    return (
      <EmptyState
        type="empty"
        title="回路不存在"
        description={`未找到 ID 为 ${loopId} 的回路`}
        action={
          <button type="button" className="btn-primary" onClick={() => navigate('/loop/monitor')}>
            返回监控列表
          </button>
        }
      />
    );
  }

  /** 构建 slot → tagName 映射 */
  const slotTagMap = new Map<string, string>();
  tags.forEach((t) => {
    const slot = t.tagName.split('-').pop();
    if (slot) slotTagMap.set(slot, t.tagName);
  });

  return (
    <div className="page-container">
      {/* 详情头部 */}
      <div className="detail-header">
        <div className="detail-header-left">
          <button
            type="button"
            className="back-btn"
            onClick={() => navigate('/loop/monitor')}
          >
            <ArrowLeft size={16} />
            <span>返回</span>
          </button>
          <div>
            <h1>{loop.loopName}</h1>
            <div className="detail-meta-row">
              <span className="mono">{loop.loopCode}</span>
              <span className="text-muted">·</span>
              <span>{loop.nodeName}</span>
              <span className="text-muted">·</span>
              <span>最近评分：{loop.lastScoredAt}</span>
            </div>
          </div>
        </div>
        <div className="detail-header-badges">
          <ControlModeBadge mode={loop.controlMode} size="md" />
          <PVQualityBadge quality={loop.pvQuality} size="md" />
          <ComputeStatusBadge status={loop.computeStatus} size="md" />
          <ScoreBadge score={loop.score} size="md" />
        </div>
      </div>

      {/* 两列：Tag 关联 + 当前值 */}
      <div className="two-col-grid">
        {/* Tag 关联信息 */}
        <div className="card">
          <div className="card-header">
            <h3>
              <Tag size={16} /> Tag 关联信息
            </h3>
            <span className={`mapping-status ${loop.mappingComplete ? 'complete' : 'incomplete'}`}>
              {loop.mappingComplete ? '关联完整' : '关联不完整'}
            </span>
          </div>
          <div className="card-body">
            <table className="tag-slot-table">
              <thead>
                <tr>
                  <th>槽位</th>
                  <th>Tag 位号</th>
                  <th>必填</th>
                </tr>
              </thead>
              <tbody>
                {TAG_SLOTS.map((slot) => {
                  const tagName = loop.tagMapping[slot.key];
                  return (
                    <tr key={slot.key}>
                      <td className="mono">{slot.key}</td>
                      <td className="mono">
                        {tagName ? (
                          <span className="tag-linked">{tagName}</span>
                        ) : (
                          <span className="tag-unlinked">— 未关联 —</span>
                        )}
                      </td>
                      <td>
                        {slot.required ? (
                          <span className="required-mark">必填</span>
                        ) : (
                          <span className="text-muted">可选</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 当前值 + 诊断 */}
        <div className="card">
          <div className="card-header">
            <h3>
              <Activity size={16} /> 当前运行状态
            </h3>
          </div>
          <div className="card-body">
            <div className="current-values-grid">
              <div className="current-value-item">
                <span className="cv-label">PV（过程变量）</span>
                <span className="cv-value mono" style={{ color: 'var(--status-ok)' }}>
                  {loop.pvValue}
                </span>
                <PVQualityBadge quality={loop.pvQuality} size="sm" />
              </div>
              <div className="current-value-item">
                <span className="cv-label">SP（设定值）</span>
                <span className="cv-value mono" style={{ color: 'var(--info)' }}>
                  {loop.spValue}
                </span>
              </div>
              <div className="current-value-item">
                <span className="cv-label">OP（操作输出）</span>
                <span className="cv-value mono" style={{ color: 'var(--warning)' }}>
                  {loop.opValue}%
                </span>
              </div>
              <div className="current-value-item">
                <span className="cv-label">控制模式</span>
                <ControlModeBadge mode={loop.controlMode} size="md" />
              </div>
            </div>

            {diagnosis && (
              <div className="diagnosis-summary-box">
                <div className="ds-header">
                  <Gauge size={14} />
                  <span>最新诊断结论</span>
                </div>
                <div className="ds-body">
                  <strong>{diagnosis.label}</strong>
                  <span className="text-muted">
                    （置信度 {Math.round(diagnosis.confidence * 100)}%）
                  </span>
                  <p className="ds-detail">{diagnosis.detail}</p>
                  <p className="ds-suggestion">
                    <strong>建议：</strong>
                    {diagnosis.suggestion}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 时序波形 */}
      <div className="card">
        <div className="card-header">
          <h3>PV / SP / OP 时序波形（30 天）</h3>
          <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
            PV 按质量码分段渲染 · LTTB 降采样
          </span>
        </div>
        <div className="card-body">
          {dataset && dataset.points.length > 0 ? (
            <WaveformChart dataset={dataset} height="360px" />
          ) : (
            <EmptyState type="empty" title="暂无时序数据" description="该回路尚未关联 PV Tag 或无历史数据" />
          )}
        </div>
      </div>

      {/* KPI 摘要 */}
      {snapshot && (
        <div className="card">
          <div className="card-header">
            <h3>KPI 分项得分</h3>
            <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
              快照时间：{snapshot.snapshotTime}
            </span>
          </div>
          <div className="card-body">
            <div className="kpi-items-grid">
              {snapshot.items.map((item, idx) => {
                const def = kpiDefinitions[idx];
                return (
                  <div key={item.kpiId} className="kpi-item-card">
                    <div className="kpi-item-header">
                      <span className="kpi-item-name">{item.kpiName}</span>
                      <span className="kpi-item-category">{def?.category}</span>
                    </div>
                    <div className="kpi-item-value mono">
                      {item.value}
                      <span className="kpi-item-unit">{item.unit}</span>
                    </div>
                    <div className="kpi-item-score">
                      <ScoreBadge score={item.score} size="sm" />
                      <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
                        权重 {def?.weight}%
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
