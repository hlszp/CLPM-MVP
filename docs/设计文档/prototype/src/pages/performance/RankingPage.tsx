/**
 * 低效回路排行页（v4.0 §6.3.2）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.3.2
 *
 * 布局结构：
 * 1. 页面标题 + 统计摘要
 * 2. FilterBar：搜索 + 节点筛选 + 评分区间筛选
 * 3. DataTable：按 score 升序（最低分在前），行级抽屉滑出回路摘要
 *
 * 设计 grammar：Lucide 图标 / 工业配色 / border + radius-md / 状态色驱动
 */

import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Eye, ArrowRight, TrendingDown, AlertCircle } from 'lucide-react';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DataTable, type Column } from '../../components/DataTable';
import { Drawer } from '../../components/Drawer';
import { PVQualityBadge, type PVQuality } from '../../components/PVQualityBadge';
import {
  ComputeStatusBadge,
  ControlModeBadge,
  ScoreBadge,
  type ComputeStatus,
  type ControlMode,
} from '../../components/StatusBadge';
import { getLoopsByScoreAsc, getLoopStats } from '../../mock/loops';
import { plantNodes } from '../../mock/plantNodes';
import { kpiSnapshots } from '../../mock/kpi';
import { findDiagnosisByLoop } from '../../mock/diagnosis';
import type { Loop } from '../../mock/types';

/** 排名行数据（DataTable 直接使用） */
interface RankingRow {
  rank: number;
  loopId: string;
  loopName: string;
  loopCode: string;
  nodeName: string;
  score: number | null;
  pvQuality: PVQuality;
  controlMode: ControlMode;
  computeStatus: ComputeStatus;
  /** 原始 Loop 引用（抽屉详情用） */
  loop: Loop;
}

/** 评分区间选项 */
const scoreRangeOptions = [
  { label: '全部', value: '' },
  { label: '低效（<60）', value: 'low' },
  { label: '关注（60-79）', value: 'mid' },
  { label: '优良（≥80）', value: 'high' },
  { label: '数据不足', value: 'null' },
];

/** 节点选项 */
const nodeOptions = plantNodes
  .filter((n) => n.nodeType === 'loop_group')
  .map((n) => ({ label: n.name, value: n.nodeId }));

export function RankingPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [nodeFilter, setNodeFilter] = useState('');
  const [scoreRange, setScoreRange] = useState('');
  const [selectedLoop, setSelectedLoop] = useState<Loop | null>(null);

  const stats = useMemo(() => getLoopStats(), []);

  /** 过滤 + 排序 + 映射为 RankingRow */
  const rows: RankingRow[] = useMemo(() => {
    let list = getLoopsByScoreAsc();

    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (l) =>
          l.loopName.toLowerCase().includes(q) ||
          l.loopCode.toLowerCase().includes(q),
      );
    }

    if (nodeFilter) {
      list = list.filter((l) => l.nodeId === nodeFilter);
    }

    if (scoreRange) {
      list = list.filter((l) => {
        if (scoreRange === 'null') return l.score === null;
        if (scoreRange === 'low') return l.score !== null && l.score < 60;
        if (scoreRange === 'mid') return l.score !== null && l.score >= 60 && l.score < 80;
        if (scoreRange === 'high') return l.score !== null && l.score >= 80;
        return true;
      });
    }

    return list.map((loop, idx) => ({
      rank: idx + 1,
      loopId: loop.loopId,
      loopName: loop.loopName,
      loopCode: loop.loopCode,
      nodeName: loop.nodeName,
      score: loop.score,
      pvQuality: loop.pvQuality,
      controlMode: loop.controlMode,
      computeStatus: loop.computeStatus,
      loop,
    }));
  }, [search, nodeFilter, scoreRange]);

  /** 选中回路的 KPI 快照 */
  const selectedSnapshot = useMemo(
    () => (selectedLoop ? kpiSnapshots.find((s) => s.loopId === selectedLoop.loopId) : null),
    [selectedLoop],
  );

  /** 选中回路的诊断 */
  const selectedDiagnosis = useMemo(
    () => (selectedLoop ? findDiagnosisByLoop(selectedLoop.loopId) : undefined),
    [selectedLoop],
  );

  /** 列定义 */
  const columns: Column<RankingRow>[] = [
    {
      key: 'rank',
      header: '排名',
      width: '60px',
      align: 'center',
      render: (row) => (
        <span className={`rank-badge ${row.rank <= 3 ? 'top' : ''} ${row.score !== null && row.score < 60 ? 'danger' : ''}`}>
          {row.rank}
        </span>
      ),
      sortable: true,
      sortValue: (row) => row.rank,
    },
    {
      key: 'loopName',
      header: '回路名',
      width: '180px',
      render: (row) => <strong>{row.loopName}</strong>,
      sortable: true,
      sortValue: (row) => row.loopName,
    },
    {
      key: 'loopCode',
      header: '位号',
      width: '120px',
      render: (row) => <span className="mono">{row.loopCode}</span>,
      sortable: true,
      sortValue: (row) => row.loopCode,
    },
    {
      key: 'nodeName',
      header: '所属节点',
      width: '140px',
      sortable: true,
      sortValue: (row) => row.nodeName,
    },
    {
      key: 'score',
      header: '评分',
      width: '80px',
      align: 'center',
      render: (row) => <ScoreBadge score={row.score} size="sm" />,
      sortable: true,
      sortValue: (row) => row.score ?? -1,
    },
    {
      key: 'pvQuality',
      header: 'PV质量',
      width: '90px',
      align: 'center',
      render: (row) => <PVQualityBadge quality={row.pvQuality} size="sm" />,
    },
    {
      key: 'controlMode',
      header: '控制模式',
      width: '80px',
      align: 'center',
      render: (row) => <ControlModeBadge mode={row.controlMode} size="sm" />,
    },
    {
      key: 'computeStatus',
      header: '计算状态',
      width: '90px',
      align: 'center',
      render: (row) => <ComputeStatusBadge status={row.computeStatus} size="sm" />,
    },
    {
      key: 'actions',
      header: '操作',
      width: '100px',
      align: 'center',
      render: (row) => (
        <div className="row-actions">
          <button
            type="button"
            className="btn-icon"
            title="查看摘要"
            onClick={(e) => {
              e.stopPropagation();
              setSelectedLoop(row.loop);
            }}
          >
            <Eye size={14} />
          </button>
          <button
            type="button"
            className="btn-icon"
            title="运行详情"
            onClick={(e) => {
              e.stopPropagation();
              navigate(`/loop/monitor/${row.loopId}`);
            }}
          >
            <ArrowRight size={14} />
          </button>
        </div>
      ),
    },
  ];

  /** 筛选项 */
  const filters: FilterItem[] = [
    {
      key: 'node',
      label: '节点',
      type: 'select',
      value: nodeFilter,
      onChange: (v) => setNodeFilter(v),
      options: nodeOptions,
    },
    {
      key: 'scoreRange',
      label: '评分区间',
      type: 'select',
      value: scoreRange,
      onChange: (v) => setScoreRange(v),
      options: scoreRangeOptions,
    },
  ];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>低效回路排行</h1>
          <p className="page-subtitle">
            按综合评分升序排列 · 低效回路优先展示 · 共 {rows.length} 条记录
          </p>
        </div>
      </div>

      {/* 统计摘要卡片 */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">低效回路（&lt;60分）</span>
            <TrendingDown size={16} className="kpi-card-icon" style={{ color: '#DC3545' }} />
          </div>
          <div className="kpi-card-value" style={{ color: '#DC3545' }}>
            {stats.lowPerf}
          </div>
          <div className="kpi-card-delta">
            占总数 {Math.round((stats.lowPerf / stats.total) * 100)}%
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">关注回路（60-79分）</span>
            <AlertCircle size={16} className="kpi-card-icon" style={{ color: '#FFC107' }} />
          </div>
          <div className="kpi-card-value" style={{ color: '#FFC107' }}>
            {stats.midPerf}
          </div>
          <div className="kpi-card-delta">需持续观察</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">优良回路（≥80分）</span>
            <TrendingDown size={16} className="kpi-card-icon" style={{ color: '#198754' }} />
          </div>
          <div className="kpi-card-value" style={{ color: '#198754' }}>
            {stats.highPerf}
          </div>
          <div className="kpi-card-delta">运行良好</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">数据不足</span>
            <AlertCircle size={16} className="kpi-card-icon" style={{ color: '#6C757D' }} />
          </div>
          <div className="kpi-card-value" style={{ color: '#6C757D' }}>
            {stats.inconclusive}
          </div>
          <div className="kpi-card-delta">需检查 Tag 关联</div>
        </div>
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索回路名 / 位号"
        filters={filters}
      />

      {/* 数据表 */}
      <DataTable
        columns={columns}
        data={rows}
        rowKey={(row) => row.loopId}
        onRowClick={(row) => setSelectedLoop(row.loop)}
        emptyText="无符合条件的回路"
      />

      {/* 抽屉：回路摘要 */}
      <Drawer
        open={!!selectedLoop}
        onClose={() => setSelectedLoop(null)}
        title={selectedLoop ? `${selectedLoop.loopName} · 回路摘要` : ''}
        width="520px"
      >
        {selectedLoop && (
          <div className="drawer-body-content">
            {/* 基本信息 */}
            <div className="form-section">
              <div className="form-section-title">基本信息</div>
              <div className="detail-meta-row">
                <span>位号：</span>
                <span className="mono">{selectedLoop.loopCode}</span>
              </div>
              <div className="detail-meta-row">
                <span>所属节点：</span>
                <span>{selectedLoop.nodeName}</span>
              </div>
              <div className="detail-meta-row">
                <span>描述：</span>
                <span>{selectedLoop.description}</span>
              </div>
              <div className="detail-meta-row">
                <span>创建时间：</span>
                <span className="mono">{selectedLoop.createdAt}</span>
              </div>
            </div>

            {/* 运行状态 */}
            <div className="form-section">
              <div className="form-section-title">运行状态</div>
              <div className="detail-meta-row">
                <span>控制模式：</span>
                <ControlModeBadge mode={selectedLoop.controlMode} size="sm" />
              </div>
              <div className="detail-meta-row">
                <span>PV 质量：</span>
                <PVQualityBadge quality={selectedLoop.pvQuality} size="sm" />
              </div>
              <div className="detail-meta-row">
                <span>计算状态：</span>
                <ComputeStatusBadge status={selectedLoop.computeStatus} size="sm" />
              </div>
              <div className="detail-meta-row">
                <span>综合评分：</span>
                <ScoreBadge score={selectedLoop.score} size="md" />
              </div>
            </div>

            {/* KPI 分项 */}
            {selectedSnapshot && (
              <div className="form-section">
                <div className="form-section-title">KPI 分项得分</div>
                <table className="kpi-detail-table">
                  <thead>
                    <tr>
                      <th>指标</th>
                      <th>实测值</th>
                      <th>得分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedSnapshot.items.map((item) => (
                      <tr key={item.kpiId}>
                        <td>{item.kpiName}</td>
                        <td className="mono">
                          {item.value} {item.unit}
                        </td>
                        <td>
                          <ScoreBadge score={item.score} size="sm" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* 诊断结论 */}
            {selectedDiagnosis && (
              <div className="form-section">
                <div className="form-section-title">最新诊断</div>
                <div className="diagnosis-box">
                  <strong>{selectedDiagnosis.label}</strong>
                  <span className="text-muted">
                    （置信度 {Math.round(selectedDiagnosis.confidence * 100)}%）
                  </span>
                  <p>{selectedDiagnosis.detail}</p>
                  <p className="ds-suggestion">
                    <strong>建议：</strong>
                    {selectedDiagnosis.suggestion}
                  </p>
                </div>
              </div>
            )}

            {/* 操作按钮 */}
            <div className="form-actions">
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  navigate(`/loop/monitor/${selectedLoop.loopId}`);
                  setSelectedLoop(null);
                }}
              >
                查看运行详情
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  navigate(`/diagnosis/${selectedLoop.loopId}`);
                  setSelectedLoop(null);
                }}
              >
                查看诊断详情
              </button>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
