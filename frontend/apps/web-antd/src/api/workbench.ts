/**
 * 工作台 v2.0 BFF API 封装（方案 §3.1 A-01~A-13 + A-E5/A-E6 增强）
 *
 * 对应后端：`app/api/v1/endpoints/workbench.py`，前缀 `/workbench`。
 * - A-10 plugins / A-12 events/read 已实现，M1 真实调用
 * - A-01~A-05 / A-07~A-09 / A-11 / A-13 为 M1 skeleton，返回空结构
 * - A-E5 unread 为 M1 桩（后端端点待 M2，前端 unreadCount 桩 0，WS 留 M2）
 */
import { requestClient } from '#/api/request';

export namespace WorkbenchApi {
  /** 范围层级（与后端 scopeType Query 对齐） */
  export type ScopeType = 'AREA' | 'FACTORY' | 'GLOBAL' | 'LOOP' | 'UNIT';

  /** 范围选择器节点（A-00 scope-tree 返回） */
  export interface ScopeNode {
    id: number;
    name: string;
    parent_id: null | string;
    parent_source_id: null | number;
    type: 'AREA' | 'FACTORY';
  }

  /** 时间窗口（24h/7d/30d；自定义窗口通过 customStart/customEnd 下发） */
  export type TimeWindow = '7d' | '24h' | '30d';

  /** 模块 4 态状态机（方案 §0.1：CORE 内置 · ENABLED 在线 · MAINTENANCE 维护 · UNINSTALLED 未安装） */
  export type ModuleStatus = 'CORE' | 'ENABLED' | 'MAINTENANCE' | 'UNINSTALLED';

  /** 维护窗口信息（MAINTENANCE 态携带，面纱/横幅展示进度） */
  export interface MaintenanceWindow {
    end_at?: string;
    message?: string;
    progress_pct?: number;
    start_at?: string;
  }

  /** 模块插件（A-10 返回项，对应 module_plugin 表序列化字段） */
  export interface Plugin {
    display_name: string;
    is_core: boolean;
    maintenance_window: MaintenanceWindow | null;
    module_key: string;
    order_index: number;
    status: ModuleStatus;
    version: null | string;
  }

  /** A-10 GET /plugins 响应 */
  export interface PluginsResult {
    plugins: Plugin[];
  }

  /** A-12 批量标记已读请求体 */
  export interface EventReadRequest {
    event_ids: number[];
  }

  /** A-12 POST /events/read 响应 */
  export interface EventReadResult {
    marked: number;
  }

  /** A-E5 未读计数响应（M1 桩：后端端点待 M2） */
  export interface UnreadResult {
    count: number;
  }

  /** 共享请求参数（scope + window + 自定义窗口） */
  export interface ScopeParams {
    customEnd?: string;
    customStart?: string;
    scopeId?: number;
    scopeType?: ScopeType;
    window?: TimeWindow;
  }

  // ---------------------------------------------------------------------------
  // A-01 /overview 强类型（G-总览 · 对齐 backend workbench_overview.py）
  // ---------------------------------------------------------------------------

  /** 6 项 KPI 键（越高越好；与后端 KPI_METRICS 对齐） */
  export type KpiMetricKey =
    | 'accuracy_rate'
    | 'auto_mode_rate'
    | 'effective_auto_rate'
    | 'fast_rate'
    | 'good_value_rate'
    | 'steady_rate';

  /** 6 项 KPI 数值（0~1 或百分比，缺失为 null → 前端 N/A 斜纹） */
  export type KpiMetrics = Partial<Record<KpiMetricKey, null | number>>;

  /** 评估状态 6 态（M-02 STATUSES） */
  export type WindowStatus =
    | 'CRITICAL'
    | 'EXCELLENT'
    | 'FAIR'
    | 'GOOD'
    | 'INCONCLUSIVE'
    | 'POOR';

  /** score_trend 点：{t: ISO, v: 0~100}（24h=24pts / 7d=7pts / 30d=15pts） */
  export interface ScoreTrendPoint {
    t: string;
    v: number;
  }

  /** M-02 内嵌 flags 简化版（完整 M-06 走 A-07 单独取） */
  export interface WindowFlag {
    desc?: string;
    kind: string;
    severity: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';
    t: string;
  }

  /** 三窗口 KPI 块（24h/7d/30d 之一；缺失数据为 null） */
  export interface WindowBlock {
    flags: WindowFlag[];
    loop_count: number;
    metrics: KpiMetrics;
    score: null | number;
    score_trend: ScoreTrendPoint[];
    snapshot_at: null | string;
    status: null | WindowStatus;
  }

  /** 装置排名行（FACTORY 行 + sparkline + lose_factors + alarm + overdue） */
  export interface PlantRow {
    alarm_count: number;
    id: null | number;
    lose_factors: string[]; // 低于阈值的 KPI 中文标签列表
    loop_count: number;
    name: string;
    overdue_tasks: number;
    rank: number;
    score: null | number;
    sparkline: ScoreTrendPoint[]; // 与 score_trend 同构，无动画渲染
    status: null | WindowStatus;
  }

  /** 单元热力行（UNIT 行 ×6 指标，缺数据 metrics[key]=null → CSS 斜纹） */
  export interface UnitRow {
    id: null | number;
    metrics: KpiMetrics;
    name: string;
    score: null | number;
    status: null | WindowStatus;
  }

  /** 异常类型分布行（MV-02 mv_diagnosis_pareto） */
  export interface ParetoRow {
    converted_count: number;
    ignored_count: number;
    root_cause: string;
    sla_warned_count: number;
    tag_count: number;
  }

  /** 根因 Top N 行（DiagnosisTag 按 tag_code 聚合，active 优先） */
  export interface RootRow {
    active_count: number;
    count: number;
    severity: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN' | null;
    tag_code: string;
    tag_name: string;
  }

  /** 处置漏斗（MV-03，4 泳道计数 + 超期 + 平均周期；缺失为 null） */
  export interface FunnelStat {
    avg_cycle_hours: null | number;
    breached: number; // 超期红底
    closed: number;
    executing: number;
    pending: number;
    reopened: number;
    verifying: number;
  }

  /** A-01 GET /overview 响应（G-总览 · 六块聚合 + 部分失败容错） */
  export interface OverviewResult {
    funnel: FunnelStat | null;
    pareto: ParetoRow[];
    plants: PlantRow[];
    roots: RootRow[];
    scope: { id: null | number; type: ScopeType };
    units: UnitRow[];
    window: TimeWindow;
    windows: Partial<Record<TimeWindow, null | WindowBlock>>;
  }

  /** A-02 GET /assessment 响应骨架 */
  export interface AssessmentResult {
    kpi_cards: unknown[];
    loops_ranked: unknown[];
    scope: { id: null | number; type: ScopeType };
    unit_heatmap: unknown[];
    window: TimeWindow;
  }

  /** A-03 GET /diagnosis 响应骨架 */
  export interface DiagnosisResult {
    concl_timeline: unknown[];
    fitness_gates: unknown[];
    open_tags: unknown[];
    rule_stats: unknown[];
    scope: { id: null | number; type: ScopeType };
    window: TimeWindow;
  }

  /** A-04 GET /tuning 响应骨架 */
  export interface TuningResult {
    batches: unknown[];
    pending_queue: unknown[];
    scope: { id: null | number; type: ScopeType };
    window: TimeWindow;
  }

  /** A-05 GET /handling 响应骨架 */
  export interface HandlingResult {
    funnel: unknown[];
    kanban: unknown[];
    reopen_list: unknown[];
    scope: { id: null | number; type: ScopeType };
    staff_load: unknown[];
    window: TimeWindow;
  }

  /** A-11 GET /aggregate 响应骨架（首屏批量预取 8 块合并 + 30s 缓存） */
  export interface AggregateResult {
    meta: {
      cache_hit: boolean;
      custom_end: null | string;
      custom_start: null | string;
      elapsed_ms: number;
      scope: { id: null | number; type: ScopeType };
      window: TimeWindow;
    };
    results: Record<string, unknown>;
  }
}

// ---------------------------------------------------------------------------
// A-10 模块 4 态列表（已实现：读 module_plugin 表）— 4 态 dot/pill 真实数据源
// ---------------------------------------------------------------------------
export function getWorkbenchPluginsApi() {
  return requestClient.get<WorkbenchApi.PluginsResult>('/workbench/plugins');
}

// ---------------------------------------------------------------------------
// A-00 范围选择器数据（工厂 + 装置列表，带 source_node_id）
// ---------------------------------------------------------------------------
export function getWorkbenchScopeTreeApi() {
  return requestClient.get<WorkbenchApi.ScopeNode[]>('/workbench/scope-tree');
}

// ---------------------------------------------------------------------------
// A-12 批量标记事件已读（已实现：调 event_bus.mark_read）
// ---------------------------------------------------------------------------
export function markWorkbenchEventsReadApi(data: WorkbenchApi.EventReadRequest) {
  return requestClient.post<WorkbenchApi.EventReadResult>(
    '/workbench/events/read',
    data,
  );
}

// ---------------------------------------------------------------------------
// A-E5 铃铛未读计数（M1 桩：后端端点待 M2，WS 推送 < 200ms 留 M2）
// ---------------------------------------------------------------------------
export function getWorkbenchUnreadApi() {
  // TODO: M2 接 WS 未读计数端点 GET /workbench/unread
  return requestClient.get<WorkbenchApi.UnreadResult>('/workbench/unread');
}

// ---------------------------------------------------------------------------
// A-01 工作台总览（三窗口 KPI + 装置/单元排名 + Pareto/根因）
// ---------------------------------------------------------------------------
export function getWorkbenchOverviewApi(params?: WorkbenchApi.ScopeParams) {
  return requestClient.get<WorkbenchApi.OverviewResult>('/workbench/overview', {
    params,
  });
}

// ---------------------------------------------------------------------------
// A-02 评估（6 项 KPI 卡片 + 单元热力 + 回路排名）
// ---------------------------------------------------------------------------
export function getWorkbenchAssessmentApi(params?: WorkbenchApi.ScopeParams) {
  return requestClient.get<WorkbenchApi.AssessmentResult>(
    '/workbench/assessment',
    { params },
  );
}

// ---------------------------------------------------------------------------
// A-03 诊断（异常回路 + 诊断结论时间线 + 适用性门禁）
// ---------------------------------------------------------------------------
export function getWorkbenchDiagnosisApi(params?: WorkbenchApi.ScopeParams) {
  return requestClient.get<WorkbenchApi.DiagnosisResult>('/workbench/diagnosis', {
    params,
  });
}

// ---------------------------------------------------------------------------
// A-04 整定（整定批次 + 待整定队列）
// ---------------------------------------------------------------------------
export function getWorkbenchTuningApi(params?: WorkbenchApi.ScopeParams) {
  return requestClient.get<WorkbenchApi.TuningResult>('/workbench/tuning', {
    params,
  });
}

// ---------------------------------------------------------------------------
// A-05 处置（看板 + 漏斗 + 人员负载 + 重开列表）
// ---------------------------------------------------------------------------
export function getWorkbenchHandlingApi(params?: WorkbenchApi.ScopeParams) {
  return requestClient.get<WorkbenchApi.HandlingResult>('/workbench/handling', {
    params,
  });
}

// ---------------------------------------------------------------------------
// A-11 首屏批量预取（8 块合并 + WBFF_CACHE 30s TTL）
// ---------------------------------------------------------------------------
export function getWorkbenchAggregateApi(params?: WorkbenchApi.ScopeParams) {
  return requestClient.get<WorkbenchApi.AggregateResult>(
    '/workbench/aggregate',
    { params },
  );
}
