/**
 * KPI 指标配置页面（UI/UX §6.3.2）
 *
 * 布局结构：
 * 1. 页面标题
 * 2. 6 大 KPI 指标配置表（DataTable）
 * 3. 权重总和显示（须 = 100%）
 * 4. 编辑 Drawer（指标名称/权重/单位/描述/启用开关）
 * 5. 保存时弹出 ConfigConfirmDialog（变更说明必填）
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 配置页：form-section + form-row + form-actions
 */

import { useMemo, useState } from 'react';
import { Pencil, Save } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { Drawer } from '../../components/Drawer';
import {
  ConfigConfirmDialog,
  type ChangeEntry,
} from '../../components/ConfigConfirmDialog';
import { useToast } from '../../components/Toast';
import { kpiDefinitions } from '../../mock/kpi';
import type { KpiDefinition } from '../../mock/types';

export default function KpiConfigPage() {
  const toast = useToast();

  /** KPI 指标列表（可编辑状态） */
  const [kpiDefs, setKpiDefs] = useState<KpiDefinition[]>(() =>
    kpiDefinitions.map((k) => ({ ...k })),
  );

  /** 当前编辑的 KPI（Drawer 表单数据） */
  const [editingKpi, setEditingKpi] = useState<KpiDefinition | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  /** 确认弹窗状态 */
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingChanges, setPendingChanges] = useState<ChangeEntry[]>([]);
  const [pendingKpi, setPendingKpi] = useState<KpiDefinition | null>(null);

  /** 权重总和 */
  const totalWeight = useMemo(
    () => kpiDefs.reduce((sum, k) => sum + k.weight, 0),
    [kpiDefs],
  );

  /** 权重是否合法（必须 = 100） */
  const isWeightValid = totalWeight === 100;

  /** 点击编辑按钮 */
  const handleEdit = (kpi: KpiDefinition) => {
    setEditingKpi({ ...kpi });
    setDrawerOpen(true);
  };

  /** Drawer 中保存：对比变更并打开确认弹窗 */
  const handleDrawerSave = () => {
    if (!editingKpi) return;
    const original = kpiDefs.find((k) => k.kpiId === editingKpi.kpiId);
    if (!original) return;

    const changes: ChangeEntry[] = [];
    if (original.kpiName !== editingKpi.kpiName) {
      changes.push({ field: '指标名称', oldValue: original.kpiName, newValue: editingKpi.kpiName });
    }
    if (original.weight !== editingKpi.weight) {
      changes.push({ field: '权重', oldValue: `${original.weight}%`, newValue: `${editingKpi.weight}%` });
    }
    if (original.unit !== editingKpi.unit) {
      changes.push({ field: '单位', oldValue: original.unit, newValue: editingKpi.unit });
    }
    if (original.description !== editingKpi.description) {
      changes.push({ field: '描述', oldValue: original.description, newValue: editingKpi.description });
    }
    if (original.enabled !== editingKpi.enabled) {
      changes.push({
        field: '启用状态',
        oldValue: original.enabled ? '启用' : '禁用',
        newValue: editingKpi.enabled ? '启用' : '禁用',
      });
    }

    if (changes.length === 0) {
      toast.warning('无变更内容');
      setDrawerOpen(false);
      return;
    }

    setPendingChanges(changes);
    setPendingKpi(editingKpi);
    setDrawerOpen(false);
    setConfirmOpen(true);
  };

  /** 确认变更：写入状态并提示 */
  const handleConfirm = (comment: string) => {
    if (!pendingKpi) return;
    setKpiDefs((prev) =>
      prev.map((k) => (k.kpiId === pendingKpi.kpiId ? pendingKpi : k)),
    );
    setConfirmOpen(false);
    setPendingKpi(null);
    setPendingChanges([]);
    toast.success(`配置已保存，变更已记录审计日志（说明：${comment}）`);
  };

  /** 表格列定义 */
  const columns: Column<KpiDefinition>[] = useMemo(
    () => [
      {
        key: 'kpiName',
        header: '指标名',
        sortable: true,
        render: (row) => (
          <div>
            <div style={{ fontWeight: 600 }}>{row.kpiName}</div>
            <div className="text-muted" style={{ fontSize: '11px' }}>
              {row.kpiCode}
            </div>
          </div>
        ),
      },
      {
        key: 'category',
        header: '类别',
        width: '90px',
        align: 'center',
        sortable: true,
        render: (row) => (
          <span className="badge status-info badge-sm">{row.category}</span>
        ),
      },
      {
        key: 'weight',
        header: '权重',
        width: '70px',
        align: 'center',
        sortable: true,
        render: (row) => (
          <span className="mono" style={{ fontWeight: 600 }}>
            {row.weight}%
          </span>
        ),
      },
      {
        key: 'unit',
        header: '单位',
        width: '60px',
        align: 'center',
        render: (row) => <span className="mono text-muted">{row.unit}</span>,
      },
      {
        key: 'description',
        header: '描述',
        render: (row) => (
          <span className="text-secondary" style={{ fontSize: '12px' }}>
            {row.description}
          </span>
        ),
      },
      {
        key: 'enabled',
        header: '启用状态',
        width: '80px',
        align: 'center',
        render: (row) =>
          row.enabled ? (
            <span className="badge status-success badge-sm">启用</span>
          ) : (
            <span className="badge status-neutral badge-sm">禁用</span>
          ),
      },
      {
        key: 'action',
        header: '操作',
        width: '70px',
        align: 'center',
        render: (row) => (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: '12px' }}
            onClick={() => handleEdit(row)}
          >
            <Pencil size={12} /> 编辑
          </button>
        ),
      },
    ],
    [],
  );

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>KPI 指标配置</h1>
          <p className="page-subtitle">
            6 大 KPI 指标定义与权重配置 · 权重总和须 = 100%
          </p>
        </div>
      </div>

      {/* 指标配置表 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>指标列表</h3>
        </div>
        <div className="form-section-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={kpiDefs}
            rowKey={(row) => row.kpiId}
          />
        </div>
        {/* 权重总和显示 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: '8px',
            padding: '12px 16px',
            borderTop: '1px solid var(--border-default)',
            fontSize: '14px',
          }}
        >
          <span className="text-muted">权重总和：</span>
          <span
            className="mono"
            style={{
              fontWeight: 700,
              fontSize: '16px',
              color: isWeightValid ? '#198754' : '#DC3545',
            }}
          >
            {totalWeight}%
          </span>
          {!isWeightValid && (
            <span
              className="badge status-danger badge-sm"
              style={{ marginLeft: '4px' }}
            >
              权重总和必须 = 100%
            </span>
          )}
        </div>
      </div>

      {/* 编辑 Drawer */}
      <Drawer
        open={drawerOpen}
        title="编辑 KPI 指标"
        onClose={() => setDrawerOpen(false)}
        footer={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setDrawerOpen(false)}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleDrawerSave}
              disabled={!editingKpi?.kpiName.trim()}
            >
              <Save size={14} /> 保存
            </button>
          </>
        }
      >
        {editingKpi && (
          <>
            <div className="form-row">
              <label>指标名称</label>
              <input
                type="text"
                value={editingKpi.kpiName}
                onChange={(e) =>
                  setEditingKpi({ ...editingKpi, kpiName: e.target.value })
                }
              />
            </div>
            <div className="form-row">
              <label>类别</label>
              <input
                type="text"
                value={editingKpi.category}
                disabled
                style={{ background: 'var(--bg-muted)', color: 'var(--text-muted)' }}
              />
            </div>
            <div className="form-row">
              <label>权重（%）</label>
              <input
                type="number"
                min={0}
                max={100}
                value={editingKpi.weight}
                onChange={(e) =>
                  setEditingKpi({
                    ...editingKpi,
                    weight: Number(e.target.value),
                  })
                }
              />
            </div>
            <div className="form-row">
              <label>单位</label>
              <input
                type="text"
                value={editingKpi.unit}
                onChange={(e) =>
                  setEditingKpi({ ...editingKpi, unit: e.target.value })
                }
              />
            </div>
            <div className="form-row">
              <label>描述</label>
              <textarea
                rows={3}
                value={editingKpi.description}
                onChange={(e) =>
                  setEditingKpi({ ...editingKpi, description: e.target.value })
                }
              />
            </div>
            <div className="form-row">
              <label>启用状态</label>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <input
                  type="checkbox"
                  checked={editingKpi.enabled}
                  onChange={(e) =>
                    setEditingKpi({ ...editingKpi, enabled: e.target.checked })
                  }
                />
                <span style={{ fontSize: '14px' }}>
                  {editingKpi.enabled ? '启用' : '禁用'}
                </span>
              </label>
            </div>
          </>
        )}
      </Drawer>

      {/* 配置变更确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName={pendingKpi?.kpiName ?? 'KPI 指标'}
        changes={pendingChanges}
        onCancel={() => {
          setConfirmOpen(false);
          setPendingKpi(null);
          setPendingChanges([]);
        }}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
