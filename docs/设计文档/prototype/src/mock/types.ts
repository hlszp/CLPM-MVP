/**
 * Mock 数据层共享类型定义
 *
 * 对齐：DDS v3.0 数据模型 + UI/UX v4.0 §13.1 字段映射速查
 *
 * 本文件定义原型用的 TypeScript 类型，字段名与 DDS 表结构对齐，
 * 便于将来切换到真实 API 时最小化改动。
 */

import type { PVQuality } from '../components/PVQualityBadge';
import type {
  ComputeStatus,
  ActionStatus,
  ControlMode,
} from '../components/StatusBadge';

/** 工厂层级节点类型（DDS plant_nodes.node_type） */
export type NodeType = 'factory' | 'unit' | 'loop_group';

/** 工厂层级节点（DDS plant_nodes） */
export interface PlantNode {
  nodeId: string;
  parentNodeId: string | null;
  nodeType: NodeType;
  name: string;
  code: string;
  sortOrder: number;
}

/** AAS 同步 Tag（DDS tag_registry） */
export interface AasTag {
  tagId: string;
  tagName: string;
  description: string;
  unit: string;
  currentValue: number | string;
  quality: PVQuality;
  /** 已关联回路 ID（null 表示未关联） */
  linkedLoopId: string | null;
  /** 已关联回路名（null 表示未关联） */
  linkedLoopName: string | null;
  lastSyncAt: string;
}

/** Tag 槽位标识（7 个 OPC Tag 槽位） */
export type TagSlotKey = 'PV' | 'SP' | 'OP' | 'MODE' | 'PID_P' | 'PID_I' | 'PID_D';

/** 回路 Tag 关联（DDS loop_tag_mapping） */
export type LoopTagMapping = Partial<Record<TagSlotKey, string>>;

/** 回路台账（DDS loops，用户创建实体） */
export interface Loop {
  loopId: string;
  loopName: string;
  loopCode: string;
  nodeId: string;
  nodeName: string;
  description: string;
  /** Tag 关联（7 槽位） */
  tagMapping: LoopTagMapping;
  /** 关联完整性：true 表示 4 个必填槽位齐全 */
  mappingComplete: boolean;
  /** 当前控制模式（from MODE tag） */
  controlMode: ControlMode;
  /** 当前 PV 值 */
  pvValue: number;
  /** 当前 PV 质量码 */
  pvQuality: PVQuality;
  /** 当前 SP 值 */
  spValue: number;
  /** 当前 OP 值 */
  opValue: number;
  /** 综合评分（0-100，null 表示数据不足） */
  score: number | null;
  /** 计算状态 */
  computeStatus: ComputeStatus;
  /** 最近一次评分时间 */
  lastScoredAt: string;
  createdAt: string;
  updatedAt: string;
}

/** KPI 指标定义（DDS kpi_definitions） */
export interface KpiDefinition {
  kpiId: string;
  kpiName: string;
  kpiCode: string;
  category: '平稳性' | '响应性' | '能耗' | '鲁棒性';
  weight: number;
  unit: string;
  description: string;
  enabled: boolean;
}

/** KPI 快照（DDS kpi_snapshot_hourly） */
export interface KpiSnapshot {
  loopId: string;
  loopName: string;
  nodeName: string;
  snapshotTime: string;
  /** 综合评分 */
  score: number | null;
  computeStatus: ComputeStatus;
  /** 各 KPI 分项得分 */
  items: Array<{
    kpiId: string;
    kpiName: string;
    kpiCode: string;
    value: number;
    score: number | null;
    unit: string;
  }>;
}

/** 诊断预诊标签（DDS diagnosis_results.diagnosis_label） */
export type DiagnosisLabel =
  | '振荡'
  | '粘滞阀'
  | '参数过激'
  | '参数过保守'
  | '外扰频繁'
  | 'PV 质量异常'
  | '人工复核';

/** 诊断结果（DDS diagnosis_results） */
export interface DiagnosisResult {
  resultId: string;
  loopId: string;
  loopName: string;
  nodeName: string;
  diagnosisTime: string;
  label: DiagnosisLabel;
  /** 置信度 0-1 */
  confidence: number;
  /** 诊断详情 */
  detail: string;
  /** 建议措施 */
  suggestion: string;
  /** 是否已生成 Action Tracker */
  hasTracker: boolean;
}

/** Action Tracker（DDS action_tracker） */
export interface ActionTracker {
  trackerId: string;
  loopId: string;
  loopName: string;
  nodeName: string;
  resultId: string;
  label: DiagnosisLabel;
  /** 处理状态 */
  actionStatus: ActionStatus;
  /** 负责人 */
  assignee: string;
  /** 处理说明 */
  comment: string;
  /** A/B 对比基准时间（RESOLVED 时填写） */
  baselineStart: string | null;
  baselineEnd: string | null;
  createdAt: string;
  updatedAt: string;
}

/** 时序数据点（含 PV 质量码，DDS timeseries_raw 降采样后） */
export interface TimeseriesPoint {
  timestamp: number;
  pv: number | null;
  sp: number | null;
  op: number | null;
  /** PV 质量码（仅 PV 有，SP/OP 不受影响） */
  pvQuality: PVQuality;
}

/** 波形数据集（回路运行详情/诊断详情用） */
export interface TimeseriesDataset {
  loopId: string;
  loopName: string;
  /** 数据点（已 LTTB 降采样，maxPoints=2000） */
  points: TimeseriesPoint[];
  /** 时间窗口 */
  windowStart: number;
  windowEnd: number;
  /** 采样点数 */
  sampleCount: number;
}

/** PV-OP 散点数据点（诊断详情用） */
export interface ScatterPoint {
  pv: number;
  op: number;
  timestamp: number;
}

/** 用户（DDS users） */
export interface User {
  userId: string;
  username: string;
  displayName: string;
  role: import('../routes/menuConfig').Role;
  email: string;
  enabled: boolean;
  createdAt: string;
  lastLoginAt: string | null;
}

/** 审计日志（DDS audit_logs） */
export interface AuditLog {
  logId: string;
  userId: string;
  username: string;
  action: string;
  resource: string;
  detail: string;
  ipAddress: string;
  createdAt: string;
}

/** 自动报表配置（DDS report_configs） */
export interface ReportConfig {
  reportId: string;
  reportName: string;
  reportType: '班报' | '日报' | '周报' | '月报';
  schedule: string;
  recipients: string[];
  enabled: boolean;
  lastGeneratedAt: string | null;
}
