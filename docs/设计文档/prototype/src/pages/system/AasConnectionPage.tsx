/**
 * AAS 连接页面（v4.0 §6.6.5）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.6.5
 *
 * 布局结构：
 * 1. 页面标题
 * 2. 顶部：AAS 连接状态卡片（连接状态/服务器地址/端口/最后同步时间/同步Tag总数）
 * 3. 中部：连接配置表单（form-section）
 * 4. 底部：Tag 同步状态表（DataTable + FilterBar）
 *
 * 交互：
 * - 保存配置：ConfigConfirmDialog 确认
 * - 操作反馈：useToast
 * - 质量码列用 PVQualityBadge 渲染
 */

import { useState, useMemo } from 'react';
import { Server, Wifi, Clock, Database, Save, Check, X } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { PVQualityBadge, type PVQuality } from '../../components/PVQualityBadge';
import { useToast } from '../../components/Toast';
import { aasTags, type AasTag } from '../../mock';

/** 连接配置表单状态 */
interface ConnectionConfig {
  serverAddress: string;
  port: string;
  username: string;
  password: string;
  syncInterval: string;
  opcServiceName: string;
}

/** 初始连接配置（mock 默认值） */
const INITIAL_CONFIG: ConnectionConfig = {
  serverAddress: '192.168.1.100',
  port: '4840',
  username: 'opcua_client',
  password: '********',
  syncInterval: '10',
  opcServiceName: 'HDS_Plant_AAS',
};

/** 连接状态信息 */
interface ConnectionStatus {
  connected: boolean;
  serverAddress: string;
  port: string;
  lastSyncAt: string;
  tagCount: number;
}

/** 质量码筛选选项 */
const QUALITY_OPTIONS: Array<{ label: string; value: PVQuality }> = [
  { label: 'Good', value: 'Good' },
  { label: 'Bad', value: 'Bad' },
  { label: 'Uncertain', value: 'Uncertain' },
];

/** 根据 Tag 质量码获取同步状态标签 */
function getSyncStatus(quality: PVQuality): { className: string; text: string } {
  switch (quality) {
    case 'Good':
      return { className: 'status-success', text: '同步正常' };
    case 'Bad':
      return { className: 'status-danger', text: '同步异常' };
    case 'Uncertain':
      return { className: 'status-warning', text: '同步不确定' };
  }
}

export default function AasConnectionPage() {
  const toast = useToast();

  // 连接配置状态
  const [config, setConfig] = useState<ConnectionConfig>(INITIAL_CONFIG);
  const [draftConfig, setDraftConfig] = useState<ConnectionConfig>(INITIAL_CONFIG);

  // 确认弹窗状态
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmChanges, setConfirmChanges] = useState<ChangeEntry[]>([]);

  // Tag 筛选状态
  const [search, setSearch] = useState('');
  const [qualityFilter, setQualityFilter] = useState('');

  // 连接状态（mock）
  const connectionStatus: ConnectionStatus = {
    connected: true,
    serverAddress: config.serverAddress,
    port: config.port,
    lastSyncAt: '2026-06-21 10:30:00',
    tagCount: aasTags.length,
  };

  /** 筛选后的 Tag 列表 */
  const filteredTags = useMemo(() => {
    return aasTags.filter((tag) => {
      const matchSearch =
        !search ||
        tag.tagId.toLowerCase().includes(search.toLowerCase()) ||
        tag.tagName.toLowerCase().includes(search.toLowerCase()) ||
        tag.description.toLowerCase().includes(search.toLowerCase());
      const matchQuality = !qualityFilter || tag.quality === qualityFilter;
      return matchSearch && matchQuality;
    });
  }, [search, qualityFilter]);

  /** 筛选项 */
  const filters: FilterItem[] = [
    {
      key: 'quality',
      label: '质量码',
      type: 'select',
      options: QUALITY_OPTIONS.map((q) => ({ label: q.label, value: q.value })),
      value: qualityFilter,
      onChange: setQualityFilter,
    },
  ];

  /** 比较配置差异 */
  const diffConfigs = (oldCfg: ConnectionConfig, newCfg: ConnectionConfig): ChangeEntry[] => {
    const changes: ChangeEntry[] = [];
    const labels: Record<keyof ConnectionConfig, string> = {
      serverAddress: '服务器地址',
      port: '端口',
      username: '用户名',
      password: '密码',
      syncInterval: '同步周期(秒)',
      opcServiceName: 'OPC 服务名',
    };
    (Object.keys(labels) as Array<keyof ConnectionConfig>).forEach((key) => {
      if (oldCfg[key] !== newCfg[key]) {
        changes.push({
          field: labels[key],
          oldValue: key === 'password' ? '********' : oldCfg[key],
          newValue: key === 'password' ? '********' : newCfg[key],
        });
      }
    });
    return changes;
  };

  /** 点击保存按钮 */
  const handleSaveClick = () => {
    const changes = diffConfigs(config, draftConfig);
    if (changes.length === 0) {
      toast.warning('配置无变更');
      return;
    }
    setConfirmChanges(changes);
    setConfirmOpen(true);
  };

  /** 确认保存配置 */
  const handleConfirm = (comment: string) => {
    setConfig(draftConfig);
    toast.success(`AAS 连接配置已保存，变更已记录审计日志（变更说明：${comment}）`);
    setConfirmOpen(false);
  };

  /** 表格列定义 */
  const columns: Column<AasTag>[] = [
    {
      key: 'tagId',
      header: 'Tag ID',
      sortable: true,
      width: '180px',
      render: (row) => <span className="mono">{row.tagId}</span>,
    },
    {
      key: 'tagName',
      header: 'Tag 名称',
      sortable: true,
      width: '180px',
      render: (row) => <span className="mono">{row.tagName}</span>,
    },
    {
      key: 'description',
      header: '描述',
      render: (row) => (
        <span
          style={{
            display: 'inline-block',
            maxWidth: '240px',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
          title={row.description}
        >
          {row.description}
        </span>
      ),
    },
    {
      key: 'currentValue',
      header: '当前值',
      width: '100px',
      align: 'right',
      render: (row) => (
        <span className="mono">
          {row.currentValue}
          {row.unit && <span className="text-muted" style={{ marginLeft: '2px' }}>{row.unit}</span>}
        </span>
      ),
    },
    {
      key: 'quality',
      header: '质量码',
      width: '110px',
      align: 'center',
      render: (row) => <PVQualityBadge quality={row.quality} size="sm" />,
    },
    {
      key: 'lastSyncAt',
      header: '最后同步时间',
      sortable: true,
      width: '160px',
      render: (row) => <span className="mono">{row.lastSyncAt}</span>,
    },
    {
      key: 'status',
      header: '状态',
      width: '110px',
      render: (row) => {
        const status = getSyncStatus(row.quality);
        return (
          <span className={`badge ${status.className} badge-sm`}>
            {row.quality === 'Good' && <Check size={12} />}
            {row.quality === 'Bad' && <X size={12} />}
            {status.text}
          </span>
        );
      },
    },
  ];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>AAS 连接</h1>
          <p className="page-subtitle">
            OPC UA AAS 连接配置与 Tag 同步状态监控
          </p>
        </div>
      </div>

      {/* 顶部：连接状态卡片 */}
      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(5, 1fr)', marginBottom: 'var(--space-4)' }}>
        {/* 连接状态 */}
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">连接状态</span>
            <Wifi
              size={16}
              style={{ color: connectionStatus.connected ? 'var(--status-ok)' : 'var(--status-danger)' }}
            />
          </div>
          <div
            className="kpi-card-value"
            style={{ color: connectionStatus.connected ? 'var(--status-ok)' : 'var(--status-danger)', fontSize: '20px' }}
          >
            {connectionStatus.connected ? '已连接' : '断开'}
          </div>
        </div>

        {/* 服务器地址 */}
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">服务器地址</span>
            <Server size={16} className="kpi-card-icon" />
          </div>
          <div className="kpi-card-value mono" style={{ fontSize: '16px' }}>
            {connectionStatus.serverAddress}
          </div>
        </div>

        {/* 端口 */}
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">端口</span>
            <Server size={16} className="kpi-card-icon" />
          </div>
          <div className="kpi-card-value mono" style={{ fontSize: '20px' }}>
            {connectionStatus.port}
          </div>
        </div>

        {/* 最后同步时间 */}
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">最后同步时间</span>
            <Clock size={16} className="kpi-card-icon" />
          </div>
          <div className="kpi-card-value mono" style={{ fontSize: '14px' }}>
            {connectionStatus.lastSyncAt}
          </div>
        </div>

        {/* 同步 Tag 总数 */}
        <div className="kpi-card">
          <div className="kpi-card-header">
            <span className="kpi-card-label">同步 Tag 总数</span>
            <Database size={16} className="kpi-card-icon" />
          </div>
          <div className="kpi-card-value">{connectionStatus.tagCount}</div>
        </div>
      </div>

      {/* 中部：连接配置表单 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>连接配置</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>服务器地址 *</label>
            <input
              type="text"
              value={draftConfig.serverAddress}
              onChange={(e) => setDraftConfig({ ...draftConfig, serverAddress: e.target.value })}
              placeholder="如：192.168.1.100"
            />
          </div>
          <div className="form-row">
            <label>端口 *</label>
            <input
              type="text"
              value={draftConfig.port}
              onChange={(e) => setDraftConfig({ ...draftConfig, port: e.target.value })}
              placeholder="如：4840"
            />
          </div>
          <div className="form-row">
            <label>用户名</label>
            <input
              type="text"
              value={draftConfig.username}
              onChange={(e) => setDraftConfig({ ...draftConfig, username: e.target.value })}
              placeholder="OPC UA 用户名"
            />
          </div>
          <div className="form-row">
            <label>密码</label>
            <input
              type="password"
              value={draftConfig.password}
              onChange={(e) => setDraftConfig({ ...draftConfig, password: e.target.value })}
              placeholder="OPC UA 密码"
            />
          </div>
          <div className="form-row">
            <label>同步周期（秒）*</label>
            <input
              type="number"
              min="1"
              value={draftConfig.syncInterval}
              onChange={(e) => setDraftConfig({ ...draftConfig, syncInterval: e.target.value })}
              placeholder="如：10"
            />
          </div>
          <div className="form-row">
            <label>OPC 服务名 *</label>
            <input
              type="text"
              value={draftConfig.opcServiceName}
              onChange={(e) => setDraftConfig({ ...draftConfig, opcServiceName: e.target.value })}
              placeholder="如：HDS_Plant_AAS"
            />
          </div>
        </div>
        <div className="form-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setDraftConfig(config)}
          >
            重置
          </button>
          <button type="button" className="btn btn-primary" onClick={handleSaveClick}>
            <Save size={14} />
            保存配置
          </button>
        </div>
      </div>

      {/* 底部：Tag 同步状态表 */}
      <div style={{ marginTop: 'var(--space-4)' }}>
        <FilterBar
          searchValue={search}
          onSearchChange={setSearch}
          searchPlaceholder="搜索 Tag ID、名称或描述..."
          filters={filters}
          showClearAll
          onClearAll={() => {
            setSearch('');
            setQualityFilter('');
          }}
        />
        <DataTable
          columns={columns}
          data={filteredTags}
          rowKey={(row) => row.tagId}
          emptyText="无符合条件的 Tag"
        />
      </div>

      {/* 保存配置确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName="AAS 连接配置"
        changes={confirmChanges}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
