/**
 * Tag 关联管理页（v4.0 §6.2.3 + §7.7）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.2.3 + §7.7
 *
 * 布局：左树右表
 * - 左侧：TreeView 显示工厂层级树，选中节点后右侧显示该节点下的回路列表
 * - 右侧：回路列表（DataTable），每行显示回路名 + 7 槽位 Tag 关联状态
 * - 点击回路行：打开 Drawer，内含 TagAssociationSelector 组件
 * - 保存时弹出 ConfigConfirmDialog（变更说明必填）
 *
 * 设计 grammar：Lucide 图标 / 工业配色 / border + radius-md / 状态色驱动
 */

import { useMemo, useState } from 'react';
import { Network, Check, X } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { TreeView, type TreeNodeData } from '../../components/TreeView';
import { Drawer } from '../../components/Drawer';
import {
  TagAssociationSelector,
  type AasTag as SelectorTag,
  type LoopTagMapping as SelectorMapping,
  type TagSlotKey,
} from '../../components/TagAssociationSelector';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { useToast } from '../../components/Toast';
import { plantNodes } from '../../mock/plantNodes';
import { loops as initialLoops } from '../../mock/loops';
import type { PlantNode, Loop } from '../../mock/types';
import { aasTags } from '../../mock/aasTags';

/** 7 槽位定义（与 TagAssociationSelector 一致） */
const ALL_SLOTS: TagSlotKey[] = ['PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D'];

/** 必填槽位 */
const REQUIRED_SLOTS: TagSlotKey[] = ['PV', 'SP', 'OP', 'MODE'];

/** PlantNode 转换为 TreeView 节点数据 */
function toTreeNodeData(nodes: PlantNode[]): TreeNodeData[] {
  const map = new Map<string, TreeNodeData>();
  nodes.forEach((n) => map.set(n.nodeId, {
    id: n.nodeId,
    name: n.name,
    type: n.nodeType,
    meta: n.code,
    children: [],
  }));
  const roots: TreeNodeData[] = [];
  nodes.forEach((n) => {
    const node = map.get(n.nodeId)!;
    if (n.parentNodeId && map.has(n.parentNodeId)) {
      map.get(n.parentNodeId)!.children!.push(node);
    } else {
      roots.push(node);
    }
  });
  return roots;
}

/** 收集节点及其所有子孙节点 ID（用于筛选回路） */
function collectDescendantIds(nodes: PlantNode[], rootId: string): Set<string> {
  const ids = new Set<string>();
  const queue = [rootId];
  while (queue.length > 0) {
    const id = queue.shift()!;
    ids.add(id);
    nodes.filter((n) => n.parentNodeId === id).forEach((c) => queue.push(c.nodeId));
  }
  return ids;
}

/** 将 mock AasTag 转换为 TagAssociationSelector 所需的 AasTag 格式 */
function toSelectorTags(): SelectorTag[] {
  return aasTags.map((t) => ({
    tagId: t.tagId,
    tagName: t.tagName,
    description: t.description,
    currentValue: t.currentValue,
    quality: t.quality,
    linkedLoop: t.linkedLoopName ?? '',
  }));
}

/** 单个槽位状态点 */
function SlotDot({ filled, required, label }: { filled: boolean; required: boolean; label: string }) {
  const color = filled
    ? 'var(--status-ok)'
    : required
      ? 'var(--status-danger)'
      : 'var(--text-muted)';
  return (
    <span
      title={`${label}: ${filled ? '已关联' : required ? '必填缺失' : '可选未关联'}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '2px',
        fontSize: '11px',
        color,
      }}
    >
      {filled ? <Check size={11} /> : required ? <X size={11} /> : <span style={{ width: '11px', display: 'inline-block' }}>-</span>}
      <span className="mono">{label}</span>
    </span>
  );
}

export function TagMappingPage() {
  const toast = useToast();
  const [loops, setLoops] = useState<Loop[]>(() => [...initialLoops]);
  // 选中节点 ID（默认根节点）
  const [selectedId, setSelectedId] = useState<string>(() => plantNodes[0].nodeId);
  // 当前编辑的回路
  const [editingLoop, setEditingLoop] = useState<Loop | null>(null);
  // 编辑中的 mapping（受控）
  const [draftMapping, setDraftMapping] = useState<SelectorMapping>({});
  // 保存前的原始 mapping（用于变更对比）
  const [originalMapping, setOriginalMapping] = useState<SelectorMapping>({});
  // 确认弹窗
  const [confirmOpen, setConfirmOpen] = useState(false);

  /** 树形数据 */
  const treeData = useMemo(() => toTreeNodeData(plantNodes), []);

  /** 选中节点对象 */
  const selectedNode = useMemo(
    () => plantNodes.find((n) => n.nodeId === selectedId),
    [selectedId],
  );

  /** 选中节点（含子孙）下的回路列表 */
  const nodeLoops = useMemo(() => {
    if (!selectedNode) return [];
    const ids = collectDescendantIds(plantNodes, selectedNode.nodeId);
    return loops.filter((l) => ids.has(l.nodeId));
  }, [loops, selectedNode]);

  /** TagAssociationSelector 用的 tag 列表 */
  const selectorTags = useMemo(() => toSelectorTags(), []);

  /** 表格列定义 */
  const columns: Column<Loop>[] = useMemo(() => [
    {
      key: 'loopName',
      header: '回路名称',
      sortable: true,
      width: '200px',
      render: (row) => <span style={{ fontWeight: 500 }}>{row.loopName}</span>,
    },
    {
      key: 'loopCode',
      header: '回路位号',
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
      key: 'tagMapping',
      header: '7 槽位 Tag 关联状态',
      width: '320px',
      sortable: true,
      sortValue: (row) => ALL_SLOTS.filter((s) => row.tagMapping[s]).length,
      render: (row) => (
        <div style={{ display: 'inline-flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          {ALL_SLOTS.map((slot) => (
            <SlotDot
              key={slot}
              filled={!!row.tagMapping[slot]}
              required={REQUIRED_SLOTS.includes(slot)}
              label={slot}
            />
          ))}
        </div>
      ),
    },
    {
      key: 'mappingComplete',
      header: '完整性',
      width: '90px',
      align: 'center',
      sortable: true,
      sortValue: (row) => (row.mappingComplete ? 1 : 0),
      render: (row) =>
        row.mappingComplete ? (
          <span style={{ color: 'var(--status-ok)', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
            <Check size={12} /> 完整
          </span>
        ) : (
          <span style={{ color: 'var(--status-danger)', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
            <X size={12} /> 缺失
          </span>
        ),
    },
  ], []);

  /** 点击回路行，打开编辑 Drawer */
  const openEditDrawer = (loop: Loop) => {
    setEditingLoop(loop);
    setOriginalMapping({ ...loop.tagMapping });
    setDraftMapping({ ...loop.tagMapping });
  };

  /** 触发保存（打开确认弹窗） */
  const handleSaveClick = () => {
    setConfirmOpen(true);
  };

  /** 确认保存 */
  const handleConfirmSave = () => {
    if (!editingLoop) return;
    const complete = REQUIRED_SLOTS.every((s) => draftMapping[s]);
    setLoops((prev) =>
      prev.map((l) =>
        l.loopId === editingLoop.loopId
          ? { ...l, tagMapping: { ...draftMapping }, mappingComplete: complete, updatedAt: new Date().toISOString().replace('T', ' ').slice(0, 19) }
          : l,
      ),
    );
    toast.success(`回路「${editingLoop.loopName}」Tag 关联已保存，变更已记录审计日志`);
    setConfirmOpen(false);
    setEditingLoop(null);
  };

  /** 计算变更条目 */
  const changes: ChangeEntry[] = useMemo(() => {
    if (!editingLoop) return [];
    const list: ChangeEntry[] = [];
    for (const slot of ALL_SLOTS) {
      const oldVal = originalMapping[slot] ?? '';
      const newVal = draftMapping[slot] ?? '';
      if (oldVal !== newVal) {
        list.push({
          field: `${slot} Tag`,
          oldValue: oldVal,
          newValue: newVal,
        });
      }
    }
    return list;
  }, [editingLoop, originalMapping, draftMapping]);

  /** 是否有变更 */
  const hasChanges = changes.length > 0;

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>Tag 关联管理</h1>
          <p className="page-subtitle">
            管理回路 7 槽位 OPC Tag 关联（PV/SP/OP/MODE/PID_P/PID_I/PID_D）· 支持拖拽与下拉双模式
          </p>
        </div>
      </div>

      {/* 左树右表布局 */}
      <div className="tree-table-layout">
        {/* 左侧：层级树 */}
        <div className="tree-panel">
          <div className="tree-panel-header">
            <h3>层级结构</h3>
            <Network size={16} className="text-muted" />
          </div>
          <TreeView
            data={treeData}
            selectedId={selectedId}
            onSelect={(node) => setSelectedId(node.id)}
          />
        </div>

        {/* 右侧：回路列表 */}
        <div className="table-panel">
          {selectedNode && (
            <div style={{ marginBottom: '12px', padding: '8px 12px', background: 'var(--bg-panel)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
              当前选中：<strong>{selectedNode.name}</strong>
              <span style={{ marginLeft: '12px' }}>下含 <strong>{nodeLoops.length}</strong> 个回路</span>
              <span style={{ marginLeft: '12px' }}>完整 <strong style={{ color: 'var(--status-ok)' }}>{nodeLoops.filter((l) => l.mappingComplete).length}</strong></span>
              <span style={{ marginLeft: '8px' }}>缺失 <strong style={{ color: 'var(--status-danger)' }}>{nodeLoops.filter((l) => !l.mappingComplete).length}</strong></span>
            </div>
          )}

          {nodeLoops.length === 0 ? (
            <div className="page-empty-state">
              <EmptyState
                type="empty"
                title="该节点下暂无回路"
                description="请在回路台账中创建回路后，再回到此页面进行 Tag 关联配置。"
              />
            </div>
          ) : (
            <DataTable
              columns={columns}
              data={nodeLoops}
              rowKey={(row) => row.loopId}
              onRowClick={openEditDrawer}
              emptyText="无回路"
            />
          )}
        </div>
      </div>

      {/* Tag 关联编辑 Drawer */}
      <Drawer
        open={!!editingLoop}
        title={`Tag 关联 - ${editingLoop?.loopName ?? ''}`}
        onClose={() => setEditingLoop(null)}
        width="960px"
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={() => setEditingLoop(null)}>
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSaveClick}
              disabled={!hasChanges}
              title={!hasChanges ? '无变更' : '保存关联'}
            >
              保存
            </button>
          </>
        }
      >
        {editingLoop && (
          <div style={{ height: 'calc(100vh - 200px)' }}>
            <div style={{ marginBottom: '12px', padding: '8px 12px', background: 'var(--bg-muted)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
              回路位号：<span className="mono">{editingLoop.loopCode}</span>
              <span style={{ marginLeft: '12px' }}>所属节点：{editingLoop.nodeName}</span>
              <span style={{ marginLeft: '12px' }}>描述：{editingLoop.description || '—'}</span>
            </div>
            <TagAssociationSelector
              tags={selectorTags}
              mapping={draftMapping}
              loopName={editingLoop.loopName}
              onChange={setDraftMapping}
            />
          </div>
        )}
      </Drawer>

      {/* 保存确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName={`回路「${editingLoop?.loopName ?? ''}」Tag 关联`}
        changes={changes}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={handleConfirmSave}
      />
    </div>
  );
}

export default TagMappingPage;
