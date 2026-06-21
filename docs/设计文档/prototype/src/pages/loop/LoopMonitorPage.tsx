/**
 * 回路监控列表页（v4.0 §6.2.4）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.2.4
 *
 * 布局：FilterBar + DataTable 列表页
 * - 列：回路名/位号/所属节点/PV值/PV质量码/SP值/OP值/控制模式/评分/计算状态/操作
 * - PV 质量码列用 PVQualityBadge 渲染
 * - 控制模式列用 ControlModeBadge 渲染
 * - 计算状态列用 ComputeStatusBadge 渲染
 * - 评分列用 ScoreBadge 渲染
 * - 筛选：搜索 + 节点 + 控制模式 + PV质量码 + 计算状态
 * - 行点击：导航到 /loop/monitor/:loopId
 * - 支持 URL 参数 ?loopId=xxx 自动高亮回路
 *
 * 设计 grammar：Lucide 图标 / 工业配色 / border + radius-md / 状态色驱动
 */

import { useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DataTable, type Column } from '../../components/DataTable';
import { PVQualityBadge, type PVQuality } from '../../components/PVQualityBadge';
import {
  ComputeStatusBadge,
  ControlModeBadge,
  ScoreBadge,
  type ComputeStatus,
  type ControlMode,
} from '../../components/StatusBadge';
import { loops } from '../../mock/loops';
import { plantNodes } from '../../mock/plantNodes';

/** 节点选项（回路组级别） */
const nodeOptions = plantNodes
  .filter((n) => n.nodeType === 'loop_group')
  .map((n) => ({ label: n.name, value: n.nodeId }));

/** 控制模式选项 */
const modeOptions: Array<{ label: string; value: ControlMode }> = [
  { label: '自动', value: 'Auto' },
  { label: '手动', value: 'Manual' },
  { label: '串级', value: 'Cascade' },
];

/** PV 质量码选项 */
const qualityOptions: Array<{ label: string; value: PVQuality }> = [
  { label: 'Good', value: 'Good' },
  { label: 'Bad', value: 'Bad' },
  { label: 'Uncertain', value: 'Uncertain' },
];

/** 计算状态选项 */
const computeStatusOptions: Array<{ label: string; value: ComputeStatus }> = [
  { label: '正常', value: 'SUCCESS' },
  { label: '部分计算', value: 'PARTIAL' },
  { label: '数据不足', value: 'INCONCLUSIVE' },
];

export function LoopMonitorPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // 从 URL 读取筛选条件与高亮回路
  const urlLoopId = searchParams.get('loopId') ?? '';
  const search = searchParams.get('search') ?? '';
  const nodeFilter = searchParams.get('node') ?? '';
  const modeFilter = searchParams.get('mode') ?? '';
  const qualityFilter = searchParams.get('quality') ?? '';
  const statusFilter = searchParams.get('status') ?? '';

  /** 更新 URL 参数（单一来源，便于分享/书签） */
  const updateParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  };

  /** 清除全部筛选 */
  const clearAll = () => {
    const next = new URLSearchParams();
    if (urlLoopId) next.set('loopId', urlLoopId);
    setSearchParams(next, { replace: true });
  };

  /** 筛选后的回路列表 */
  const filteredLoops = useMemo(() => {
    let result = [...loops];
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (l) => l.loopName.toLowerCase().includes(q) || l.loopCode.toLowerCase().includes(q),
      );
    }
    if (nodeFilter) {
      result = result.filter((l) => l.nodeId === nodeFilter);
    }
    if (modeFilter) {
      result = result.filter((l) => l.controlMode === modeFilter);
    }
    if (qualityFilter) {
      result = result.filter((l) => l.pvQuality === qualityFilter);
    }
    if (statusFilter) {
      result = result.filter((l) => l.computeStatus === statusFilter);
    }
    return result;
  }, [search, nodeFilter, modeFilter, qualityFilter, statusFilter]);

  /** 筛选项配置 */
  const filters: FilterItem[] = [
    {
      key: 'node',
      label: '节点',
      type: 'select',
      options: nodeOptions,
      value: nodeFilter,
      onChange: (v) => updateParam('node', v),
    },
    {
      key: 'mode',
      label: '控制模式',
      type: 'select',
      options: modeOptions,
      value: modeFilter,
      onChange: (v) => updateParam('mode', v),
    },
    {
      key: 'quality',
      label: 'PV 质量码',
      type: 'select',
      options: qualityOptions,
      value: qualityFilter,
      onChange: (v) => updateParam('quality', v),
    },
    {
      key: 'status',
      label: '计算状态',
      type: 'select',
      options: computeStatusOptions,
      value: statusFilter,
      onChange: (v) => updateParam('status', v),
    },
  ];

  /** 表格列定义 */
  const columns: Column<typeof loops[number]>[] = useMemo(() => [
    {
      key: 'loopName',
      header: '回路名称',
      sortable: true,
      width: '200px',
      render: (row) => <span style={{ fontWeight: 500 }}>{row.loopName}</span>,
    },
    {
      key: 'loopCode',
      header: '位号',
      sortable: true,
      width: '160px',
      render: (row) => <span className="mono">{row.loopCode}</span>,
    },
    {
      key: 'nodeName',
      header: '所属节点',
      sortable: true,
      width: '110px',
    },
    {
      key: 'pvValue',
      header: 'PV 值',
      sortable: true,
      width: '90px',
      align: 'right',
      sortValue: (row) => row.pvValue,
      render: (row) => <span className="mono">{row.pvValue.toFixed(1)}</span>,
    },
    {
      key: 'pvQuality',
      header: 'PV 质量码',
      sortable: true,
      width: '110px',
      render: (row) => <PVQualityBadge quality={row.pvQuality} />,
    },
    {
      key: 'spValue',
      header: 'SP 值',
      sortable: true,
      width: '90px',
      align: 'right',
      sortValue: (row) => row.spValue,
      render: (row) => <span className="mono">{row.spValue.toFixed(1)}</span>,
    },
    {
      key: 'opValue',
      header: 'OP 值',
      sortable: true,
      width: '90px',
      align: 'right',
      sortValue: (row) => row.opValue,
      render: (row) => <span className="mono">{row.opValue.toFixed(1)}</span>,
    },
    {
      key: 'controlMode',
      header: '控制模式',
      sortable: true,
      width: '90px',
      render: (row) => <ControlModeBadge mode={row.controlMode} />,
    },
    {
      key: 'score',
      header: '评分',
      sortable: true,
      width: '70px',
      align: 'center',
      sortValue: (row) => row.score ?? -1,
      render: (row) => <ScoreBadge score={row.score} />,
    },
    {
      key: 'computeStatus',
      header: '计算状态',
      sortable: true,
      width: '100px',
      render: (row) => <ComputeStatusBadge status={row.computeStatus} />,
    },
    {
      key: 'actions',
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
            navigate(`/loop/monitor/${row.loopId}`);
          }}
          title="查看运行详情"
        >
          <ArrowRight size={12} />
        </button>
      ),
    },
  ], [navigate]);

  /** 行点击导航到运行详情页 */
  const handleRowClick = (loop: typeof loops[number]) => {
    navigate(`/loop/monitor/${loop.loopId}`);
  };

  /** URL 高亮回路提示 */
  const highlightedLoop = urlLoopId ? loops.find((l) => l.loopId === urlLoopId) : null;

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>回路监控列表</h1>
          <p className="page-subtitle">
            全厂控制回路实时监控 · 共 {loops.length} 个回路 · 异常 PV {loops.filter((l) => l.pvQuality !== 'Good').length} · 低效（&lt;60）{loops.filter((l) => l.score !== null && l.score < 60).length}
          </p>
        </div>
      </div>

      {/* URL 高亮提示条 */}
      {highlightedLoop && (
        <div
          style={{
            marginBottom: '12px',
            padding: '8px 12px',
            background: 'var(--accent-blue-bg)',
            border: '1px solid rgba(13,110,253,0.3)',
            borderRadius: 'var(--radius-md)',
            fontSize: 'var(--text-small)',
            color: 'var(--accent-blue)',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <span>已通过 URL 参数定位回路：</span>
          <strong>{highlightedLoop.loopName}</strong>
          <span className="mono">({highlightedLoop.loopCode})</span>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginLeft: 'auto', padding: '2px 10px', fontSize: '12px' }}
            onClick={() => navigate(`/loop/monitor/${highlightedLoop.loopId}`)}
          >
            查看详情 <ArrowRight size={12} />
          </button>
        </div>
      )}

      {/* 筛选栏 + 列表 */}
      <FilterBar
        searchValue={search}
        onSearchChange={(v) => updateParam('search', v)}
        searchPlaceholder="搜索回路名称/位号"
        filters={filters}
        showClearAll={!!(search || nodeFilter || modeFilter || qualityFilter || statusFilter)}
        onClearAll={clearAll}
      />

      <DataTable
        columns={columns}
        data={filteredLoops}
        rowKey={(row) => row.loopId}
        onRowClick={handleRowClick}
        emptyText="无匹配回路"
      />
    </div>
  );
}

export default LoopMonitorPage;
