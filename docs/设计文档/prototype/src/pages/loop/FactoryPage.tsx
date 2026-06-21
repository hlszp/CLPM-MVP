/**
 * 工厂层级配置页（v4.0 §6.2.1）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.2.1
 *
 * 布局：左树右表（tree-table-layout）
 * - 左侧：TreeView 显示工厂层级树（工厂→装置→回路组），支持选中节点
 * - 右侧：选中节点的子节点列表（DataTable），支持新增/编辑/删除
 * - 顶部：FilterBar（搜索 + 节点类型筛选）
 * - 新增/编辑用 Drawer 抽屉表单（节点名称、编码、类型、排序号）
 * - 删除需 ConfigConfirmDialog 确认
 *
 * 设计 grammar：Lucide 图标 / 工业配色 / border + radius-md / 状态色驱动
 */

import { useMemo, useState } from 'react';
import { Plus, Pencil, Trash2, Network } from 'lucide-react';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DataTable, type Column } from '../../components/DataTable';
import { Drawer } from '../../components/Drawer';
import { TreeView, type TreeNodeData } from '../../components/TreeView';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { useToast } from '../../components/Toast';
import { plantNodes } from '../../mock/plantNodes';
import type { PlantNode, NodeType } from '../../mock/types';

/** 节点类型中文标签 */
const NODE_TYPE_LABEL: Record<NodeType, string> = {
  factory: '工厂',
  unit: '装置',
  loop_group: '回路组',
};

/** 节点类型选项 */
const NODE_TYPE_OPTIONS = [
  { label: '工厂', value: 'factory' },
  { label: '装置', value: 'unit' },
  { label: '回路组', value: 'loop_group' },
];

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

/** 表单数据 */
interface NodeFormData {
  nodeId?: string;
  name: string;
  code: string;
  nodeType: NodeType;
  sortOrder: number;
  parentNodeId: string | null;
}

const EMPTY_FORM: NodeFormData = {
  name: '',
  code: '',
  nodeType: 'loop_group',
  sortOrder: 1,
  parentNodeId: null,
};

export function FactoryPage() {
  const toast = useToast();
  // 本地节点列表（原型支持增删改）
  const [nodes, setNodes] = useState<PlantNode[]>(() => [...plantNodes]);
  // 选中节点 ID（默认根节点）
  const [selectedId, setSelectedId] = useState<string>(() => plantNodes[0].nodeId);
  // 搜索关键词
  const [search, setSearch] = useState('');
  // 节点类型筛选
  const [typeFilter, setTypeFilter] = useState('');
  // Drawer 状态
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<'add' | 'edit'>('add');
  const [formData, setFormData] = useState<NodeFormData>(EMPTY_FORM);
  // 删除确认弹窗
  const [deleteTarget, setDeleteTarget] = useState<PlantNode | null>(null);

  /** 树形数据 */
  const treeData = useMemo(() => toTreeNodeData(nodes), [nodes]);

  /** 选中节点对象 */
  const selectedNode = useMemo(
    () => nodes.find((n) => n.nodeId === selectedId),
    [nodes, selectedId],
  );

  /** 选中节点的子节点列表（应用筛选） */
  const childNodes = useMemo(() => {
    if (!selectedNode) return [];
    let children = nodes.filter((n) => n.parentNodeId === selectedNode.nodeId);
    if (search) {
      const q = search.toLowerCase();
      children = children.filter(
        (n) => n.name.toLowerCase().includes(q) || n.code.toLowerCase().includes(q),
      );
    }
    if (typeFilter) {
      children = children.filter((n) => n.nodeType === typeFilter);
    }
    return [...children].sort((a, b) => a.sortOrder - b.sortOrder);
  }, [nodes, selectedNode, search, typeFilter]);

  /** 表格列定义 */
  const columns: Column<PlantNode>[] = useMemo(() => [
    {
      key: 'name',
      header: '节点名称',
      sortable: true,
      width: '30%',
      render: (row) => <span style={{ fontWeight: 500 }}>{row.name}</span>,
    },
    {
      key: 'code',
      header: '编码',
      sortable: true,
      width: '15%',
      render: (row) => <span className="mono">{row.code}</span>,
    },
    {
      key: 'nodeType',
      header: '类型',
      sortable: true,
      width: '15%',
      render: (row) => NODE_TYPE_LABEL[row.nodeType],
    },
    {
      key: 'sortOrder',
      header: '排序号',
      sortable: true,
      width: '10%',
      align: 'center',
      render: (row) => <span className="mono">{row.sortOrder}</span>,
    },
    {
      key: 'nodeId',
      header: '节点 ID',
      width: '15%',
      render: (row) => <span className="mono">{row.nodeId}</span>,
    },
    {
      key: 'actions',
      header: '操作',
      width: '120px',
      align: 'center',
      render: (row) => (
        <div style={{ display: 'inline-flex', gap: '4px' }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: '12px' }}
            onClick={(e) => {
              e.stopPropagation();
              openEditDrawer(row);
            }}
            title="编辑"
          >
            <Pencil size={12} />
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: '12px', color: 'var(--status-danger)' }}
            onClick={(e) => {
              e.stopPropagation();
              setDeleteTarget(row);
            }}
            title="删除"
          >
            <Trash2 size={12} />
          </button>
        </div>
      ),
    },
  ], []);

  /** 筛选项配置 */
  const filters: FilterItem[] = [
    {
      key: 'type',
      label: '类型',
      type: 'select',
      options: NODE_TYPE_OPTIONS,
      value: typeFilter,
      onChange: setTypeFilter,
    },
  ];

  /** 打开新增 Drawer */
  const openAddDrawer = () => {
    if (!selectedNode) return;
    // 根据父节点类型推断子节点类型
    const childType: NodeType = selectedNode.nodeType === 'factory' ? 'unit' : 'loop_group';
    const maxOrder = Math.max(
      0,
      ...nodes.filter((n) => n.parentNodeId === selectedNode.nodeId).map((n) => n.sortOrder),
    );
    setFormData({
      ...EMPTY_FORM,
      nodeType: childType,
      parentNodeId: selectedNode.nodeId,
      sortOrder: maxOrder + 1,
    });
    setDrawerMode('add');
    setDrawerOpen(true);
  };

  /** 打开编辑 Drawer */
  const openEditDrawer = (node: PlantNode) => {
    setFormData({
      nodeId: node.nodeId,
      name: node.name,
      code: node.code,
      nodeType: node.nodeType,
      sortOrder: node.sortOrder,
      parentNodeId: node.parentNodeId,
    });
    setDrawerMode('edit');
    setDrawerOpen(true);
  };

  /** 生成新节点 ID */
  const genNodeId = (type: NodeType): string => {
    const prefix = type === 'factory' ? 'F' : type === 'unit' ? 'U' : 'G';
    const existing = nodes
      .filter((n) => n.nodeId.startsWith(prefix))
      .map((n) => parseInt(n.nodeId.slice(1), 10))
      .filter((n) => !isNaN(n));
    const next = existing.length > 0 ? Math.max(...existing) + 1 : 1;
    return `${prefix}${String(next).padStart(3, '0')}`;
  };

  /** 保存节点（新增/编辑） */
  const handleSave = () => {
    if (!formData.name.trim()) {
      toast.error('节点名称不能为空');
      return;
    }
    if (!formData.code.trim()) {
      toast.error('节点编码不能为空');
      return;
    }

    if (drawerMode === 'add') {
      const newNode: PlantNode = {
        nodeId: genNodeId(formData.nodeType),
        parentNodeId: formData.parentNodeId,
        nodeType: formData.nodeType,
        name: formData.name.trim(),
        code: formData.code.trim(),
        sortOrder: formData.sortOrder,
      };
      setNodes((prev) => [...prev, newNode]);
      toast.success(`节点「${newNode.name}」已创建`);
    } else {
      setNodes((prev) =>
        prev.map((n) =>
          n.nodeId === formData.nodeId
            ? {
                ...n,
                name: formData.name.trim(),
                code: formData.code.trim(),
                nodeType: formData.nodeType,
                sortOrder: formData.sortOrder,
              }
            : n,
        ),
      );
      toast.success(`节点「${formData.name}」已更新`);
    }
    setDrawerOpen(false);
  };

  /** 删除节点（含子节点递归删除） */
  const handleDelete = () => {
    if (!deleteTarget) return;
    // 收集所有子孙节点 ID
    const toDelete = new Set<string>();
    const collect = (id: string) => {
      toDelete.add(id);
      nodes.filter((n) => n.parentNodeId === id).forEach((c) => collect(c.nodeId));
    };
    collect(deleteTarget.nodeId);

    const count = toDelete.size;
    setNodes((prev) => prev.filter((n) => !toDelete.has(n.nodeId)));
    // 如果删除的是当前选中节点，回退到根节点
    if (toDelete.has(selectedId)) {
      setSelectedId(plantNodes[0].nodeId);
    }
    toast.success(`节点「${deleteTarget.name}」${count > 1 ? `及其 ${count - 1} 个子节点` : ''}已删除`);
    setDeleteTarget(null);
  };

  /** 删除变更摘要 */
  const deleteChanges: ChangeEntry[] = deleteTarget
    ? [
        { field: '节点名称', oldValue: deleteTarget.name, newValue: '（删除）' },
        { field: '节点编码', oldValue: deleteTarget.code, newValue: '（删除）' },
        { field: '节点类型', oldValue: NODE_TYPE_LABEL[deleteTarget.nodeType], newValue: '（删除）' },
      ]
    : [];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>工厂层级配置</h1>
          <p className="page-subtitle">
            管理工厂→装置→回路组三级层级结构 · 共 {nodes.length} 个节点
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

        {/* 右侧：子节点列表 */}
        <div className="table-panel">
          <FilterBar
            searchValue={search}
            onSearchChange={setSearch}
            searchPlaceholder="搜索节点名称/编码"
            filters={filters}
            showClearAll={!!(search || typeFilter)}
            onClearAll={() => {
              setSearch('');
              setTypeFilter('');
            }}
            actions={
              <button
                type="button"
                className="btn btn-primary"
                onClick={openAddDrawer}
                disabled={!selectedNode || selectedNode.nodeType === 'loop_group'}
                title={selectedNode?.nodeType === 'loop_group' ? '回路组下不可再创建子节点' : '新增子节点'}
              >
                <Plus size={14} />
                新增子节点
              </button>
            }
          />

          {selectedNode && (
            <div style={{ marginBottom: '12px', padding: '8px 12px', background: 'var(--bg-panel)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
              当前选中：<strong>{selectedNode.name}</strong>
              <span className="mono" style={{ marginLeft: '8px', color: 'var(--text-muted)' }}>{selectedNode.code}</span>
              <span style={{ marginLeft: '8px' }}>类型：{NODE_TYPE_LABEL[selectedNode.nodeType]}</span>
            </div>
          )}

          {selectedNode?.nodeType === 'loop_group' ? (
            <div className="page-empty-state">
              <EmptyState
                type="empty"
                title="回路组为叶子节点"
                description="回路组下不可再创建子节点，请在回路台账中创建回路。"
              />
            </div>
          ) : childNodes.length === 0 ? (
            <div className="page-empty-state">
              <EmptyState
                type="empty"
                title="暂无子节点"
                description="点击右上角「新增子节点」创建第一个子节点。"
              />
            </div>
          ) : (
            <DataTable
              columns={columns}
              data={childNodes}
              rowKey={(row) => row.nodeId}
              initialSortKey="sortOrder"
            />
          )}
        </div>
      </div>

      {/* 新增/编辑 Drawer */}
      <Drawer
        open={drawerOpen}
        title={drawerMode === 'add' ? '新增节点' : '编辑节点'}
        onClose={() => setDrawerOpen(false)}
        width="480px"
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={() => setDrawerOpen(false)}>
              取消
            </button>
            <button type="button" className="btn btn-primary" onClick={handleSave}>
              保存
            </button>
          </>
        }
      >
        <div className="form-section">
          <div className="form-section-header">
            <h3>节点信息</h3>
          </div>
          <div className="form-section-body">
            <div className="form-row">
              <label>节点名称 <span style={{ color: 'var(--status-danger)' }}>*</span></label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="如：加氢精制装置"
              />
            </div>
            <div className="form-row">
              <label>节点编码 <span style={{ color: 'var(--status-danger)' }}>*</span></label>
              <input
                type="text"
                value={formData.code}
                onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                placeholder="如：HDS"
                className="mono"
              />
            </div>
            <div className="form-row">
              <label>节点类型</label>
              <select
                value={formData.nodeType}
                onChange={(e) => setFormData({ ...formData, nodeType: e.target.value as NodeType })}
              >
                {NODE_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label>排序号</label>
              <input
                type="number"
                min={1}
                value={formData.sortOrder}
                onChange={(e) => setFormData({ ...formData, sortOrder: parseInt(e.target.value, 10) || 1 })}
                className="mono"
              />
            </div>
            <div className="form-row">
              <label>父节点</label>
              <input
                type="text"
                value={
                  formData.parentNodeId
                    ? nodes.find((n) => n.nodeId === formData.parentNodeId)?.name ?? '—'
                    : '（根节点）'
                }
                disabled
              />
            </div>
          </div>
        </div>
      </Drawer>

      {/* 删除确认弹窗 */}
      <ConfigConfirmDialog
        key={deleteTarget?.nodeId ?? 'closed'}
        open={!!deleteTarget}
        configName={`删除节点「${deleteTarget?.name ?? ''}」`}
        changes={deleteChanges}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => handleDelete()}
      />
    </div>
  );
}

export default FactoryPage;
