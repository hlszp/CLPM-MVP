/**
 * CLPM 回路管理 API（对齐 IDS v3.2 §2.2）
 *
 * 覆盖回路台账 CRUD、Tag 关联管理、回路监控三类子能力。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace LoopApi {
  /** 回路状态（IDS v3.2 §2.2.7） */
  export type LoopStatus = 'INACTIVE' | 'PARTIAL' | 'READY';

  /** 可信度等级（A-E） */
  export type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E';

  /** 控制方式（IDS v3.2 §2.2.7） */
  export type ControlMode = 'Auto' | 'Cascade' | 'Manual';

  /** Tag 角色（IDS v3.2 §2.2.12） */
  export type TagRole =
    | 'MODE'
    | 'OP'
    | 'PID_D'
    | 'PID_I'
    | 'PID_P'
    | 'PV'
    | 'SP';

  /** 质量码（IDS v3.2 §2.2.5） */
  export type Quality = 'BAD' | 'GOOD' | 'UNCERTAIN' | null;

  /** 趋势时间窗（IDS v3.2 §2.2.14）
   *  WS-D 阶段5：移除 last_7_days（后端 TREND_WINDOWS 不支持，仅诊断/看板维度使用 7 天窗） */
  export type TrendWindow =
    | 'last_1_hour'
    | 'last_2_hours'
    | 'last_4_hours'
    | 'last_8_hours'
    | 'last_24_hours'
    | 'last_72_hours';

  /** KPI 状态（IDS v3.2 §2.2.14） */
  export type KpiStatus = 'INCONCLUSIVE' | 'PARTIAL' | 'SUCCESS';

  /** 评分权重（6 大 KPI，总和须 100，对齐 GB/T 44693.2-2024）
   * @deprecated v6.1：回路级权重未参与 KPI 计算，统一由 MetricConfig.weight 全局配置管理。
   * 保留字段仅为兼容后端响应，前端不再写入。
   */
  export interface ScoreWeights {
    /** 自动模式率权重 */
    auto_mode_rate: number;
    /** 稳定率权重 */
    steady_rate: number;
    /** 准确度权重 */
    accuracy_rate: number;
    /** 快速率权重 */
    fast_rate: number;
    /** 振荡率权重 */
    oscillation_rate: number;
    /** 饱和率权重 */
    saturation_rate: number;
  }

  /** Tag 关联完整性状态（7 个槽位） */
  export interface TagMappingStatus {
    pv: boolean;
    sp: boolean;
    op: boolean;
    mode: boolean;
    pid_p: boolean;
    pid_i: boolean;
    pid_d: boolean;
  }

  /** 回路类型（TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER） */
  export type LoopType =
    | 'ANALYSIS'
    | 'FLOW'
    | 'LEVEL'
    | 'OTHER'
    | 'PRESSURE'
    | 'SPEED'
    | 'TEMPERATURE';

  /** 量程信息（从关联 Tag 引用，不冗余存储） */
  export interface RangeInfo {
    min: null | number;
    max: null | number;
  }

  /** 复杂回路角色：MAIN(主回路,聚合代表) / SUB(副回路)；NULL=普通单回路 */
  export type ComplexRole = 'MAIN' | 'SUB';

  /** 回路列表项（IDS v3.2 §2.2.7） */
  export interface LoopListItem {
    loopId: string;
    tagName: string;
    description: string;
    unitId: string;
    unitName: string;
    controlMode: ControlMode;
    loopType?: LoopType;
    /** 控制类型（STABLE/SLOW/FAST/LOGIC），用于评分权重分类 */
    controlType?: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';
    /** 回路级别（1/2/3），用于级别权重评分 */
    importanceLevel?: 1 | 2 | 3;
    includeInEvaluation?: boolean | null;
    isActive: boolean;
    status: LoopStatus;
    /**
     * 综合评分（v6.1：回路管理列表已移除该列）
     * @deprecated v6.1 后端仍返回此字段（来自 loop.score_weight 僵尸字段，恒为 null），
     * 前端不再使用。综合评分请走回路监控接口 MonitorListItem.score。
     */
    score?: number;
    /** @deprecated v6.1 同 score，已废弃 */
    lastScoreAt?: string;
    tagMappingStatus: TagMappingStatus;
    /** v6.1 新增：PV 量程（从 PV Tag 引用） */
    pvRange?: null | RangeInfo;
    /** v6.1 新增：PV 工程单位 */
    pvUnit?: null | string;
    /** v6.1 新增：OP 量程（从 OP Tag 引用） */
    opRange?: null | RangeInfo;
    /** v6.1 新增：OP 工程单位 */
    opUnit?: null | string;
    /** v6.1 新增：OP 输出下限位（NULL 时取 OP Tag range_min） */
    opOutputLowerLimit?: null | number;
    /** v6.1 新增：OP 输出上限位（NULL 时取 OP Tag range_max） */
    opOutputUpperLimit?: null | number;
    /** v6.1 新增：关联 DCS 型号 ID（NULL=使用本系统默认 MODE 映射） */
    dcsModelId?: null | string;
    /** 理想稳态时间（秒），空则按控制类型默认值 */
    idealSettlingTime?: null | number;
    /** P4 S4：复杂回路分组 ID（同 ID 归为一个物理控制回路，NULL=普通单回路） */
    complexLoopGroupId?: null | string;
    /** P4 S4：复杂回路角色（MAIN/SUB，NULL=普通单回路） */
    complexRole?: ComplexRole | null;
  }

  /** 回路列表查询参数（IDS v3.2 §2.2.7） */
  export interface LoopQueryParams extends PageQuery {
    plantNodeId?: string;
    controlMode?: ControlMode;
    loopType?: LoopType;
    /** 控制类型筛选 */
    controlType?: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';
    /** 级别筛选 */
    importanceLevel?: 1 | 2 | 3;
    /** 监控状态筛选（true=监控中/false=已停用） */
    monitorStatus?: boolean;
    isActive?: boolean;
    status?: LoopStatus;
    keyword?: string;
    /** 参评状态筛选（v5.3：true=参评/false=不参评） */
    includeInEvaluation?: boolean;
  }

  /** 创建回路参数（IDS v3.2 §2.2.8） */
  export interface CreateLoopParams {
    tagName: string;
    description?: string;
    unitId: string;
    loopType?: LoopType;
    /** 控制类型 */
    controlType?: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';
    /** 回路级别 */
    importanceLevel?: 1 | 2 | 3;
    /** 是否参与评估（v5.3：默认 true） */
    includeInEvaluation?: boolean;
    scoreWeights?: ScoreWeights;
    isActive?: boolean;
    remark?: string;
    /** v6.1 新增：OP 输出下限位 */
    opOutputLowerLimit?: null | number;
    /** v6.1 新增：OP 输出上限位 */
    opOutputUpperLimit?: null | number;
    /** v6.1 新增：关联 DCS 型号 ID */
    dcsModelId?: null | string;
    /** 理想稳态时间（秒），空则按控制类型默认值 */
    idealSettlingTime?: null | number;
    /** P4 S4：复杂回路分组 ID（NULL=普通单回路） */
    complexLoopGroupId?: null | string;
    /** P4 S4：复杂回路角色（MAIN/SUB，NULL=普通单回路） */
    complexRole?: ComplexRole | null;
  }

  /** 更新回路参数（IDS v3.2 §2.2.10） */
  export interface UpdateLoopParams {
    description?: string;
    unitId?: string;
    loopType?: LoopType;
    /** 控制类型 */
    controlType?: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';
    /** 回路级别 */
    importanceLevel?: 1 | 2 | 3;
    /** 是否参与评估（v5.3） */
    includeInEvaluation?: boolean;
    scoreWeights?: ScoreWeights;
    isActive?: boolean;
    remark?: string;
    /** v6.1 新增：OP 输出下限位 */
    opOutputLowerLimit?: null | number;
    /** v6.1 新增：OP 输出上限位 */
    opOutputUpperLimit?: null | number;
    /** v6.1 新增：关联 DCS 型号 ID */
    dcsModelId?: null | string;
    /** 理想稳态时间（秒），空则按控制类型默认值 */
    idealSettlingTime?: null | number;
    /** P4 S4：复杂回路分组 ID（PUT null 可清空，解除分组） */
    complexLoopGroupId?: null | string;
    /** P4 S4：复杂回路角色（PUT null 可清空，解除分组） */
    complexRole?: ComplexRole | null;
  }

  /** 创建回路响应（IDS v3.2 §2.2.8） */
  export interface CreateLoopResult {
    loopId: string;
    tagName: string;
    description: string;
    unitId: string;
    status: LoopStatus;
    isActive: boolean;
    scoreWeights: ScoreWeights;
    remark?: string;
    createdAt: string;
    createdBy: string;
    /** P4 S4：复杂回路分组 ID */
    complexLoopGroupId?: null | string;
    /** P4 S4：复杂回路角色 */
    complexRole?: ComplexRole | null;
  }

  /** 更新回路响应（IDS v3.2 §2.2.10） */
  export interface UpdateLoopResult {
    loopId: string;
    description: string;
    scoreWeights: ScoreWeights;
    isActive: boolean;
    remark?: string;
    updatedAt: string;
    updatedBy: string;
    /** P4 S4：复杂回路分组 ID */
    complexLoopGroupId?: null | string;
    /** P4 S4：复杂回路角色 */
    complexRole?: ComplexRole | null;
  }

  /** 删除回路响应（IDS v3.2 §2.2.11） */
  export interface DeleteLoopResult {
    loopId: string;
    deleted: boolean;
    deletedAt: string;
  }

  /** 单个 Tag 关联信息（IDS v3.2 §2.2.9） */
  export interface TagMappingItem {
    tagId: null | string;
    tagName: null | string;
    required: boolean;
    associated: boolean;
  }

  /** 回路详情 - 基础信息（IDS v3.2 §2.2.9） */
  export interface LoopBasicInfo {
    loopId: string;
    tagName: string;
    description: string;
    unitId: string;
    unitName: string;
    isActive: boolean;
    status: LoopStatus;
    loopType?: LoopType;
    scoreWeights: ScoreWeights;
    remark?: string;
    createdAt: string;
    createdBy: string;
    updatedAt: string;
    updatedBy: string;
    /** v6.1 新增：PV 量程（从 PV Tag 引用） */
    pvRange?: null | RangeInfo;
    /** v6.1 新增：PV 工程单位 */
    pvUnit?: null | string;
    /** v6.1 新增：OP 量程（从 OP Tag 引用） */
    opRange?: null | RangeInfo;
    /** v6.1 新增：OP 工程单位 */
    opUnit?: null | string;
    /** v6.1 新增：OP 输出下限位（NULL 时取 OP Tag range_min） */
    opOutputLowerLimit?: null | number;
    /** v6.1 新增：OP 输出上限位（NULL 时取 OP Tag range_max） */
    opOutputUpperLimit?: null | number;
    /** v6.1 新增：关联 DCS 型号 ID（NULL=使用本系统默认 MODE 映射） */
    dcsModelId?: null | string;
    /** 理想稳态时间（秒），空则按控制类型默认值 */
    idealSettlingTime?: null | number;
    /** P4 S4：复杂回路分组 ID */
    complexLoopGroupId?: null | string;
    /** P4 S4：复杂回路角色 */
    complexRole?: ComplexRole | null;
  }

  /** 回路详情 - Tag 关联映射（IDS v3.2 §2.2.9） */
  export interface LoopTagMapping {
    pv: TagMappingItem;
    sp: TagMappingItem;
    op: TagMappingItem;
    mode: TagMappingItem;
    pid_p: TagMappingItem;
    pid_i: TagMappingItem;
    pid_d: TagMappingItem;
  }

  /** 回路详情 - 运行态参数（IDS v3.2 §2.2.9） */
  export interface LoopRuntimeParams {
    controlMode: ControlMode;
    pidP: number;
    pidI: number;
    pidD: number;
    readAt: string;
  }

  /** 回路详情 - AAS 同步状态（IDS v3.2 §2.2.9） */
  export interface LoopAasSyncStatus {
    lastSyncAt: string;
    associatedTagCount: number;
  }

  /** 回路详情（IDS v3.2 §2.2.9） */
  export interface LoopDetail {
    basicInfo: LoopBasicInfo;
    tagMapping: LoopTagMapping;
    runtimeParams: LoopRuntimeParams;
    aasSyncStatus: LoopAasSyncStatus;
  }

  /** Tag 关联详情项（IDS v3.2 §2.2.12） */
  export interface LoopTagDetail {
    role: TagRole;
    tagId: null | string;
    tagName: null | string;
    description: null | string;
    required: boolean;
    associated: boolean;
    currentValue: null | number;
    quality: Quality;
    lastSyncAt: null | string;
  }

  /** Tag 关联详情（IDS v3.2 §2.2.12） */
  export interface LoopTagsResult {
    loopId: string;
    tagName: string;
    status: LoopStatus;
    tags: LoopTagDetail[];
  }

  /** Tag 关联更新参数（IDS v3.2 §2.2.13） */
  export interface UpdateTagMappingParams {
    pv: null | string;
    sp: null | string;
    op: null | string;
    mode: null | string;
    pid_p: null | string;
    pid_i: null | string;
    pid_d: null | string;
  }

  /** 监控列表项 - 当前值（IDS v3.2 §2.2.15） */
  export interface MonitorCurrentValues {
    pv: number;
    sp: number;
    op: number;
    mode: number;
    modeLabel: string;
    pvQuality: Quality;
    readAt?: string;
    /** 工程单位（从 Tag 关联获取，如 °C、MPa、% 等） */
    unit?: string;
  }

  /** 回路监控列表项（IDS v3.2 §2.2.15） */
  export interface MonitorListItem {
    loopId: string;
    tagName: string;
    description: string;
    unitName: string;
    /** v6.1：PV 量程（从 PV Tag 引用） */
    pvRange?: null | RangeInfo;
    /** v6.1：PV 工程单位 */
    pvUnit?: null | string;
    /** v6.1：OP 量程（从 OP Tag 引用） */
    opRange?: null | RangeInfo;
    /** v6.1：OP 工程单位 */
    opUnit?: null | string;
    currentValues: MonitorCurrentValues;
    controlMode: ControlMode;
    loopType?: LoopType;
    score: number;
    /** C1-1 增量巡检："较昨日"评分增量（无基线/无当前评分时为 null） */
    scoreDelta?: null | number;
    /** C1-1 增量巡检：趋势 NEW/WORSENED/IMPROVED/FLAT（无当前评分时为 null） */
    dayTrend?: 'FLAT' | 'IMPROVED' | 'NEW' | 'WORSENED' | null;
    /** 回路状态：READY/PARTIAL/INACTIVE（WS-D 阶段5：拆分 status） */
    loopStatus: LoopStatus;
    /** KPI 快照状态：SUCCESS/PARTIAL/INCONCLUSIVE（WS-D 阶段5：拆分 status） */
    kpiStatus?: KpiStatus;
    confidenceLevel?: ConfidenceLevel;
    effectiveAutoRate?: number;
    kpiSummary?: KpiSummary;
    isActive: boolean;
    readAt: string;
    /**
     * 数据健康度（方案 A §5）：预处理 validRate + 可信度 + 完整度
     * 来自最新 KPI 快照 / 每日巡检快照，列表页不实时查 TDengine
     */
    dataHealth?: LoopDataHealth;
  }

  /** 回路数据健康度（预处理有效率 + 可信度 + 完整度） */
  export interface LoopDataHealth {
    /** 预处理：好值率/有效率（0~1，来自预处理管道 + ConfidenceEvaluator） */
    validRate?: null | number;
    /** 回路可信度：A/B/C/D/E 等级 */
    confidenceLevel?: ConfidenceLevel | null;
    /** 数据完整性：PV 列完整度（0~1，来自每日 02:00 巡检快照） */
    pvCompleteness?: null | number;
    /** 整体完整度（0~1） */
    overallCompleteness?: null | number;
    /** 完整性状态：OK/WARNING/CRITICAL/DATA_UNAVAILABLE */
    integrityStatus?: null | string;
    /** 缺失列列表 */
    missingColumns?: null | string[];
    /** 最近巡检日期（ISO 串） */
    lastIntegrityCheck?: null | string;
  }

  /** 回路监控列表查询参数（IDS v3.2 §2.2.15） */
  export interface MonitorQueryParams extends PageQuery {
    plantNodeId?: string;
    view?: 'card' | 'list';
    loopType?: LoopType;
    keyword?: string;
    /**
     * 精确查询指定回路（MW-P0-02）：供深链接解析，命中只返回目标回路，
     * 未命中返回空，不回退其他回路。
     */
    loopId?: string;
  }

  /** 回路监控列表聚合统计（E-1，服务端返回，不分页全量范围） */
  export interface MonitorAggregate {
    /** 五档评分计数 + INCONCLUSIVE（无评分） */
    gradeCounts: Record<'EXCELLENT' | 'GOOD' | 'FAIR' | 'WARNING' | 'POOR' | 'INCONCLUSIVE', number>;
    /** 简单平均评分（仅计有评分回路，1位小数） */
    avgScore: number | null;
    /** 有评分回路数（avgScore 分母） */
    scoredCount: number;
    /** 较昨日恶化（scoreDelta ≤ -2）回路数 */
    worsenedCount: number;
    /** MODE 实时分布（AUTO/CAS/MAN/REMOTE/ADVANCED/UNKNOWN） */
    modeDistribution: Record<string, number>;
    /** 实时自控率（AUTO+CAS+REMOTE+ADVANCED 占比，百分比，1位小数） */
    autoControlRate: number;
  }

  /** 回路监控列表响应（IDS v3.2 §2.2.15） */
  export interface MonitorListResult {
    view: 'card' | 'list';
    items: MonitorListItem[];
    total: number;
    page: number;
    pageSize: number;
    /** E-1 聚合统计（深链接 loop_id 精确查询时为 null） */
    aggregate: MonitorAggregate | null;
  }

  /** 回路监控详情 - 趋势数据（IDS v3.2 §2.2.14） */
  export interface MonitorTrend {
    timestamps: number[];
    pv: (null | number)[];
    sp: (null | number)[];
    op: (null | number)[];
    mode: (null | number)[];
    pvQuality: Quality[];
    /** 采样间隔（秒），由后端根据时间范围动态计算（如 72h → 72s） */
    sampleInterval?: number;
    /** 是否触发了 LTTB 降采样 */
    downsampled?: boolean;
  }

  /** 回路监控详情 - KPI 摘要（IDS v3.2 §2.2.14） */
  export interface KpiSummary {
    good_value_rate: number;
    auto_mode_rate: number;
    effective_auto_rate: number;
    steady_rate: number;
    accuracy_rate: number;
    fast_rate: number;
    oscillation_rate: number;
    saturation_rate: number;
    composite_score: number;
    status: KpiStatus;
    algorithm_version: string;
    calculatedAt: string;
    /**
     * 回路级可信度等级（A/B/C/D/E），由后端 _aggregate_kpi_snapshots 返回。
     * 可信度统一 Phase 2：detail 页可信度徽章统一用此字段，不再前端推导。
     */
    confidence_level?: null | string;
  }

  /** 回路监控详情（IDS v3.2 §2.2.14） */
  export interface MonitorDetail {
    loopId: string;
    tagName: string;
    /** 回路状态：READY/PARTIAL/INACTIVE（WS-D 阶段5：拆分 status） */
    loopStatus: LoopStatus;
    /** KPI 快照状态：SUCCESS/PARTIAL/INCONCLUSIVE（WS-D 阶段5：拆分 status） */
    kpiStatus?: KpiStatus;
    currentValues: MonitorCurrentValues & { readAt: string };
    runtimeParams: {
      controlMode: ControlMode;
      pidD: number;
      pidI: number;
      pidP: number;
    };
    trend: MonitorTrend;
    kpiSummary: KpiSummary;
  }

  /** 投用定义控制模式（AUTO/CAS/REMOTE/APC/MANUAL） */
  export type ModeMappingControlMode =
    | 'APC'
    | 'AUTO'
    | 'CAS'
    | 'MANUAL'
    | 'REMOTE';

  /** 投用定义条目（MODE 值 → 控制模式映射） */
  export interface ModeMappingItem {
    /** DCS 系统返回的 MODE 原始值（整数或字符串） */
    modeValue: string;
    /** 控制模式 */
    controlMode: ModeMappingControlMode;
    /** 是否视为自动（参与自控率统计） */
    isAuto: boolean;
    /** 是否有效（无效值将被忽略） */
    isEnabled: boolean;
    /** 备注 */
    remark?: string;
  }

  /** 投用定义列表响应 */
  export interface ModeMappingResult {
    loopId: string;
    items: ModeMappingItem[];
    updatedAt?: string;
    updatedBy?: string;
  }

  /** 投用定义更新参数 */
  export interface UpdateModeMappingParams {
    items: ModeMappingItem[];
  }

  /** 批量配置更新字段（至少一个非空） */
  export interface LoopBatchUpdates {
    /** 是否监控（is_active=True 表示启用监控） */
    isMonitored?: boolean;
    /** 是否纳入统计 */
    isStatEnabled?: boolean;
    /** 回路级别 1/2/3 */
    importanceLevel?: 1 | 2 | 3;
    /** 是否参与评估（v5.3） */
    includeInEvaluation?: boolean;
  }

  /** 批量配置请求（更新模式 / 删除模式互斥） */
  export interface LoopBatchConfigParams {
    /** 回路 ID 列表（不能为空） */
    loopIds: string[];
    /** 批量更新字段（与 action 互斥） */
    updates?: LoopBatchUpdates;
    /** 批量动作：delete=软删除（与 updates 互斥） */
    action?: 'delete';
  }

  /** 批量配置响应 */
  export interface LoopBatchConfigResult {
    /** 受影响的回路数量 */
    affected: number;
    /** 执行的动作：update/delete */
    action: 'delete' | 'update';
    /** 受影响的回路 ID 列表 */
    loopIds: string[];
  }

  /** P4 S4：批量分组请求参数 */
  export interface LoopBatchGroupingParams {
    /** 回路 ID 列表（2-20 个） */
    loopIds: string[];
    /** 主回路 ID（须在 loopIds 中，设为 MAIN） */
    mainLoopId: string;
  }

  /** P4 S4：批量分组后单回路分配结果 */
  export interface LoopGroupAssignment {
    loopId: string;
    tagName: string;
    /** 分组角色：MAIN(主回路) / SUB(副回路) */
    role: ComplexRole;
  }

  /** P4 S4：批量分组响应 */
  export interface LoopBatchGroupingResult {
    /** 新生成的分组 ID */
    groupId: string;
    /** 受影响的回路数 */
    affected: number;
    /** 各回路的分组角色分配（MAIN 在前） */
    assignments: LoopGroupAssignment[];
  }

  /** P4 S4：复杂回路分组列表项 */
  export interface ComplexGroupItem {
    /** 分组 ID */
    groupId: string;
    /** 主回路位号（MAIN 回路的 tagName，无 MAIN 时为 null） */
    mainTagName: null | string;
    /** 组成员数 */
    memberCount: number;
  }
}

/**
 * 获取回路列表（分页）— IDS v3.2 §2.2.7
 */
export function getLoopListApi(params: LoopApi.LoopQueryParams) {
  return requestClient.get<PaginatedResponse<LoopApi.LoopListItem>>('/loops', {
    params,
  });
}

/**
 * 创建回路 — IDS v3.2 §2.2.8
 */
export function createLoopApi(data: LoopApi.CreateLoopParams) {
  return requestClient.post<LoopApi.CreateLoopResult>('/loops', data);
}

/**
 * 获取回路详情 — IDS v3.2 §2.2.9
 */
export function getLoopDetailApi(loopId: string) {
  return requestClient.get<LoopApi.LoopDetail>(`/loops/${loopId}`);
}

/**
 * 更新回路 — IDS v3.2 §2.2.10
 */
export function updateLoopApi(loopId: string, data: LoopApi.UpdateLoopParams) {
  return requestClient.put<LoopApi.UpdateLoopResult>(`/loops/${loopId}`, data);
}

/**
 * 删除回路 — IDS v3.2 §2.2.11
 */
export function deleteLoopApi(loopId: string) {
  return requestClient.delete<LoopApi.DeleteLoopResult>(`/loops/${loopId}`);
}

/**
 * 获取回路关联的 Tag 列表 — IDS v3.2 §2.2.12
 */
export function getLoopTagsApi(loopId: string) {
  return requestClient.get<LoopApi.LoopTagsResult>(`/loops/${loopId}/tags`);
}

/**
 * 更新回路 Tag 关联 — IDS v3.2 §2.2.13
 */
export function updateLoopTagMappingApi(
  loopId: string,
  data: LoopApi.UpdateTagMappingParams,
) {
  return requestClient.put<LoopApi.LoopTagsResult>(
    `/loops/${loopId}/tags`,
    data,
  );
}

/**
 * 获取回路运行态数据（详情） — IDS v3.2 §2.2.14
 */
export function getLoopMonitorDetailApi(
  loopId: string,
  trendWindow: LoopApi.TrendWindow = 'last_24_hours',
) {
  return requestClient.get<LoopApi.MonitorDetail>(`/loops/${loopId}/monitor`, {
    params: { trendWindow },
  });
}

/**
 * 获取回路监控列表 — IDS v3.2 §2.2.15
 */
export function getLoopMonitorListApi(params: LoopApi.MonitorQueryParams) {
  return requestClient.get<LoopApi.MonitorListResult>('/loops/monitor', {
    params,
  });
}

/**
 * 获取回路投用定义（MODE → 控制模式映射） — IDS v3.2 §2.2.13
 */
export function getLoopModeMappingApi(loopId: string) {
  return requestClient.get<LoopApi.ModeMappingResult>(
    `/loops/${loopId}/mode-mapping`,
  );
}

/**
 * 更新回路投用定义（MODE → 控制模式映射） — IDS v3.2 §2.2.13
 */
export function updateLoopModeMappingApi(
  loopId: string,
  data: LoopApi.UpdateModeMappingParams,
) {
  return requestClient.put<LoopApi.ModeMappingResult>(
    `/loops/${loopId}/mode-mapping`,
    data,
  );
}

/**
 * 获取回路类型统计（支持递归子节点）
 */
export interface LoopTypeStats {
  [key: string]: number;
  TEMPERATURE: number;
  PRESSURE: number;
  LEVEL: number;
  FLOW: number;
  ANALYSIS: number;
  SPEED: number;
  OTHER: number;
}

export function getLoopTypeStatsApi(plantNodeId?: string) {
  return requestClient.get<LoopTypeStats>('/loops/monitor/stats', {
    params: plantNodeId ? { plantNodeId } : undefined,
  });
}

/**
 * 批量配置回路（监控/统计/级别 / 批量软删除） — 配置增强
 *
 * 两种模式（互斥）：
 * - 更新模式：提供 updates 字段
 * - 删除模式：action="delete"
 *
 * 仅 ADMIN 可调用。
 */
export function batchConfigLoopsApi(data: LoopApi.LoopBatchConfigParams) {
  return requestClient.post<LoopApi.LoopBatchConfigResult>(
    '/loops/batch-config',
    data,
  );
}

/**
 * 批量建立复杂回路分组（P4 S4） — 将 2-20 个回路归为一个复杂控制回路，
 * 系统自动生成 group ID，指定一个 MAIN 回路（聚合代表），其余自动为 SUB。
 *
 * 仅 ADMIN / IC_ENGINEER 可调用。
 */
export function batchGroupLoopsApi(data: LoopApi.LoopBatchGroupingParams) {
  return requestClient.post<LoopApi.LoopBatchGroupingResult>(
    '/loops/batch-grouping',
    data,
  );
}

/**
 * 查询所有复杂回路分组（P4 S4） — 返回分组列表，含主回路位号与组成员数。
 */
export function getComplexGroupsApi() {
  return requestClient.get<LoopApi.ComplexGroupItem[]>('/loops/complex-groups');
}
