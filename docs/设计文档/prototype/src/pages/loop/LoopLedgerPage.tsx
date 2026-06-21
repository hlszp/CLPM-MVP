/**
 * 回路台账页（v4.0 §6.2.2）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.2.2
 *
 * 布局：FilterBar + DataTable 列表页
 * - 列：回路位号/名称/所属节点/控制模式/Tag关联完整性/评分/计算状态/创建时间/操作
 * - 筛选：搜索（名称/位号）+ 节点下拉 + 控制模式下拉 + 计算状态下拉
 * - 行点击：打开 Drawer 显示回路详情摘要
 * - 新增回路按钮：打开 Drawer 表单
 * - Tag 关联完整性列：绿色✓/红色✗标识 4 个必填槽位（PV/SP/OP/MODE）
 *
 * 设计 grammar：Lucide 图标 / 工业配色 / border + radius-md / 状态色驱动
 */

import { useMemo, useState } from 'react';
import { Plus, Check, X, Eye } from 'lucide-react';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DataTable, type Column } from '../../components/DataTable';
import { Drawer } from '../../components/Drawer';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { useToast } from '../../components/Toast';
import {
  ComputeStatusBadge,
  ControlModeBadge,
  ScoreBadge,
  type ComputeStatus,
  type ControlMode,
} from '../../components/StatusBadge';
import { loops as initialLoops } from '../../mock/loops';
import type { Loop, TagSlotKey } from '../../mock/types';
import { plantNodes } from '../../mock/plantNodes';

/** 必填槽位列表 */
const REQUIRED_SLOTS: Array<{ key: TagSlotKey; label: string }> = [
  { key: 'PV', label: 'PV' },
  { key: 'SP', label: 'SP' },
  { key: 'OP', label: 'OP' },
  { key: 'MODE', label: 'MODE' },
];

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

/** 计算状态选项 */
const computeStatusOptions: Array<{ label: string; value: ComputeStatus }> = [
  { label: '正常', value: 'SUCCESS' },
  { label: '部分计算', value: 'PARTIAL' },
  { label: '数据不足', value: 'INCONCLUSIVE' },
];

/** 新增回路表单数据 */
interface LoopFormData {
  loopName: string;
  loopCode: string;
  nodeId: string;
  description: string;
}

const EMPTY_FORM: LoopFormData = {
  loopName: '',
  loopCode: '',
  nodeId: nodeOptions[0]?.value ?? '',
  description: '',
};

/** 单个槽位完整性标识 */
function SlotIndicator({ filled, label }: { filled: boolean; label: string }) {
  return (
    <span
      title={`${label}: ${filled ? '已关联' : '未关联'}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '2px',
        fontSize: '11px',
        color: filled ? 'var(--status-ok)' : 'var(--status-danger)',
      }}
    >
      {filled ? <Check size={12} /> : <X size={12} />}
      <span className="mono">{label}</span>
    </span>
  );
}

export function LoopLedgerPage() {
  const toast = useToast();
  const [loops, setLoops] = useState<Loop[]>(() => [...initialLoops]);
  // 筛选状态
  const [search, setSearch] = useState('');
  const [nodeFilter, setNodeFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  // Drawer 状态
  const [detailLoop, setDetailLoop] = useState<Loop | null>(null);
  const [addDrawerOpen, setAddDrawerOpen] = useState(false);
  const [formData, setFormData] = useState<LoopFormData>(EMPTY_FORM);
  // 新增确认弹窗
  const [confirmOpen, setConfirmOpen] = useState(false);

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
    if (statusFilter) {
      result = result.filter((l) => l.computeStatus === statusFilter);
    }
    return result;
  }, [loops, search, nodeFilter, modeFilter, statusFilter]);

  /** 筛选项配置 */
  const filters: FilterItem[] = [
    {
      key: 'node',
      label: '节点',
      type: 'select',
      options: nodeOptions,
      value: nodeFilter,
      onChange: setNodeFilter,
    },
    {
      key: 'mode',
      label: '控制模式',
      type: 'select',
      options: modeOptions,
      value: modeFilter,
      onChange: setModeFilter,
    },
    {
      key: 'status',
      label: '计算状态',
      type: 'select',
      options: computeStatusOptions,
      value: statusFilter,
      onChange: setStatusFilter,
    },
  ];

  /** 表格列定义 */
  const columns: Column<Loop>[] = useMemo(() => [
    {
      key: 'loopCode',
      header: '回路位号',
      sortable: true,
      width: '160px',
      render: (row) => <span className="mono">{row.loopCode}</span>,
    },
    {
      key: 'loopName',
      header: '回路名称',
      sortable: true,
      width: '200px',
      render: (row) => <span style={{ fontWeight: 500 }}>{row.loopName}</span>,
    },
    {
      key: 'nodeName',
      header: '所属节点',
      sortable: true,
      width: '110px',
    },
    {
      key: 'controlMode',
      header: '控制模式',
      sortable: true,
      width: '90px',
      render: (row) => <ControlModeBadge mode={row.controlMode} />,
    },
    {
      key: 'mappingComplete',
      header: 'Tag 关联完整性',
      width: '180px',
      sortable: true,
      sortValue: (row) => REQUIRED_SLOTS.filter((s) => row.tagMapping[s.key]).length,
      render: (row) => (
        <div style={{ display: 'inline-flex', gap: '8px', alignItems: 'center' }}>
          {REQUIRED_SLOTS.map((slot) => (
            <SlotIndicator
              key={slot.key}
              filled={!!row.tagMapping[slot.key]}
              label={slot.label}
            />
          ))}
        </div>
      ),
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
      key: 'createdAt',
      header: '创建时间',
      sortable: true,
      width: '150px',
      render: (row) => <span className="mono">{row.createdAt}</span>,
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
            setDetailLoop(row);
          }}
          title="查看详情"
        >
          <Eye size={12} />
        </button>
      ),
    },
  ], []);

  /** 提交新增回路 */
  const handleSubmitNew = () => {
    if (!formData.loopName.trim()) {
      toast.error('回路名称不能为空');
      return;
    }
    if (!formData.loopCode.trim()) {
      toast.error('回路位号不能为空');
      return;
    }
    setConfirmOpen(true);
  };

  /** 确认新增 */
  const handleConfirmAdd = () => {
    const parentNode = plantNodes.find((n) => n.nodeId === formData.nodeId);
    const newLoop: Loop = {
      loopId: `L${String(loops.length + 1).padStart(3, '0')}`,
      loopName: formData.loopName.trim(),
      loopCode: formData.loopCode.trim(),
      nodeId: formData.nodeId,
      nodeName: parentNode?.name ?? '—',
      description: formData.description.trim(),
      tagMapping: {},
      mappingComplete: false,
      controlMode: 'Manual',
      pvValue: 0,
      pvQuality: 'Good',
      spValue: 0,
      opValue: 0,
      score: null,
      computeStatus: 'INCONCLUSIVE',
      lastScoredAt: '—',
      createdAt: new Date().toISOString().replace('T', ' ').slice(0, 19),
      updatedAt: new Date().toISOString().replace('T', ' ').slice(0, 19),
    };
    setLoops((prev) => [...prev, newLoop]);
    toast.success(`回路「${newLoop.loopName}」已创建，请前往 Tag 关联管理完成 7 槽位配置`);
    setConfirmOpen(false);
    setAddDrawerOpen(false);
    setFormData(EMPTY_FORM);
  };

  /** 新增变更摘要 */
  const addChanges: ChangeEntry[] = [
    { field: '回路名称', oldValue: '—', newValue: formData.loopName },
    { field: '回路位号', oldValue: '—', newValue: formData.loopCode },
    { field: '所属节点', oldValue: '—', newValue: nodeOptions.find((n) => n.value === formData.nodeId)?.label ?? '—' },
    { field: '描述', oldValue: '—', newValue: formData.description || '—' },
  ];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>回路台账</h1>
          <p className="page-subtitle">
            管理全厂控制回路 · 共 {loops.length} 个回路 · 完整 {loops.filter((l) => l.mappingComplete).length} / 缺失 {loops.filter((l) => !l.mappingComplete).length}
          </p>
        </div>
      </div>

      {/* 筛选栏 + 列表 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索回路名称/位号"
        filters={filters}
        showClearAll={!!(search || nodeFilter || modeFilter || statusFilter)}
        onClearAll={() => {
          setSearch('');
          setNodeFilter('');
          setModeFilter('');
          setStatusFilter('');
        }}
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setAddDrawerOpen(true)}>
            <Plus size={14} />
            新增回路
          </button>
        }
      />

      <DataTable
        columns={columns}
        data={filteredLoops}
        rowKey={(row) => row.loopId}
        onRowClick={(row) => setDetailLoop(row)}
        emptyText="无匹配回路"
      />

      {/* 回路详情 Drawer */}
      <Drawer
        open={!!detailLoop}
        title="回路详情"
        onClose={() => setDetailLoop(null)}
        width="480px"
      >
        {detailLoop && (
          <div>
            {/* 基本信息 */}
            <div className="form-section">
              <div className="form-section-header">
                <h3>基本信息</h3>
              </div>
              <div className="form-section-body">
                <div className="form-row">
                  <label>回路名称</label>
                  <span style={{ fontWeight: 500 }}>{detailLoop.loopName}</span>
                </div>
                <div className="form-row">
                  <label>回路位号</label>
                  <span className="mono">{detailLoop.loopCode}</span>
                </div>
                <div className="form-row">
                  <label>所属节点</label>
                  <span>{detailLoop.nodeName}</span>
                </div>
                <div className="form-row">
                  <label>控制模式</label>
                  <ControlModeBadge mode={detailLoop.controlMode} />
                </div>
                <div className="form-row">
                  <label>描述</label>
                  <span style={{ color: 'var(--text-secondary)' }}>{detailLoop.description || '—'}</span>
                </div>
                <div className="form-row">
                  <label>创建时间</label>
                  <span className="mono">{detailLoop.createdAt}</span>
                </div>
                <div className="form-row">
                  <label>更新时间</label>
                  <span className="mono">{detailLoop.updatedAt}</span>
                </div>
              </div>
            </div>

            {/* Tag 关联状态 */}
            <div className="form-section">
              <div className="form-section-header">
                <h3>Tag 关联状态</h3>
              </div>
              <div className="form-section-body">
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
                  {REQUIRED_SLOTS.map((slot) => {
                    const filled = !!detailLoop.tagMapping[slot.key];
                    return (
                      <span
                        key={slot.key}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '4px 10px',
                          borderRadius: 'var(--radius-pill)',
                          fontSize: 'var(--text-small)',
                          border: `1px solid ${filled ? 'rgba(25,135,84,0.3)' : 'rgba(220,53,69,0.3)'}`,
                          background: filled ? 'rgba(25,135,84,0.12)' : 'rgba(220,53,69,0.12)',
                          color: filled ? 'var(--status-ok)' : 'var(--status-danger)',
                        }}
                      >
                        {filled ? <Check size={12} /> : <X size={12} />}
                        <span className="mono">{slot.label}</span>
                      </span>
                    );
                  })}
                </div>
                <div style={{ fontSize: 'var(--text-small)', color: detailLoop.mappingComplete ? 'var(--status-ok)' : 'var(--status-danger)' }}>
                  {detailLoop.mappingComplete
                    ? '✓ 必填槽位完整，可进入性能评估'
                    : '✗ 必填槽位缺失，请前往 Tag 关联管理完成配置'}
                </div>
              </div>
            </div>

            {/* 最近评分 */}
            <div className="form-section">
              <div className="form-section-header">
                <h3>最近评分</h3>
              </div>
              <div className="form-section-body">
                <div className="form-row">
                  <label>综合评分</label>
                  <ScoreBadge score={detailLoop.score} size="md" />
                </div>
                <div className="form-row">
                  <label>计算状态</label>
                  <ComputeStatusBadge status={detailLoop.computeStatus} />
                </div>
                <div className="form-row">
                  <label>评分时间</label>
                  <span className="mono">{detailLoop.lastScoredAt}</span>
                </div>
              </div>
            </div>
          </div>
        )}
      </Drawer>

      {/* 新增回路 Drawer */}
      <Drawer
        open={addDrawerOpen}
        title="新增回路"
        onClose={() => setAddDrawerOpen(false)}
        width="480px"
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={() => setAddDrawerOpen(false)}>
              取消
            </button>
            <button type="button" className="btn btn-primary" onClick={handleSubmitNew}>
              提交
            </button>
          </>
        }
      >
        <div className="form-section">
          <div className="form-section-header">
            <h3>回路信息</h3>
          </div>
          <div className="form-section-body">
            <div className="form-row">
              <label>回路名称 <span style={{ color: 'var(--status-danger)' }}>*</span></label>
              <input
                type="text"
                value={formData.loopName}
                onChange={(e) => setFormData({ ...formData, loopName: e.target.value })}
                placeholder="如：R-101 反应器入口温度"
              />
            </div>
            <div className="form-row">
              <label>回路位号 <span style={{ color: 'var(--status-danger)' }}>*</span></label>
              <input
                type="text"
                value={formData.loopCode}
                onChange={(e) => setFormData({ ...formData, loopCode: e.target.value })}
                placeholder="如：HDS-RX-TIC-101"
                className="mono"
              />
            </div>
            <div className="form-row">
              <label>所属节点 <span style={{ color: 'var(--status-danger)' }}>*</span></label>
              <select
                value={formData.nodeId}
                onChange={(e) => setFormData({ ...formData, nodeId: e.target.value })}
              >
                {nodeOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label>描述</label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="回路用途/工艺说明"
                rows={3}
                style={{ resize: 'vertical' }}
              />
            </div>
          </div>
        </div>
        <div style={{ padding: '0 var(--space-4)', fontSize: 'var(--text-small)', color: 'var(--text-muted)' }}>
          提示：创建后回路默认控制模式为「手动」，Tag 关联为空，需前往 Tag 关联管理完成 7 槽位配置后才能进入性能评估。
        </div>
      </Drawer>

      {/* 新增确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName="新增回路"
        changes={addChanges}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={handleConfirmAdd}
      />
    </div>
  );
}

export default LoopLedgerPage;
