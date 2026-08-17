/**
 * 诊断模块 API（MVP v2 重设计版）
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.1
 * 后端：backend/app/api/v1/endpoints/diagnosis_v2.py（/api/v1/diagnosis/*）
 */

import type { PageQuery, PaginatedResponse } from '#/api/types';
import { requestClient } from '#/api/request';

export namespace DiagnosisApi {
  /** 原因分类代码（8 类，设计 §3.1）；展示元数据见 views/diagnosis/constants.ts */
  export type Category =
    | 'TUNING'
    | 'VALVE'
    | 'INSTRUMENT'
    | 'COMMUNICATION'
    | 'PROCESS'
    | 'UTILIZATION'
    | 'DESIGN'
    | 'DATA_INSUFFICIENT';

  export type Severity = 'HIGH' | 'MEDIUM' | 'LOW';
  export type RunStatus = 'FAILED' | 'PARTIAL' | 'RUNNING' | 'SUCCESS';
  /** 触发类型：手动 / 分级定时 / 预警事件（§12 三层自动诊断） */
  export type TriggerType = 'EVENT' | 'MANUAL' | 'SCHEDULED';

  /** 分类判定（主/次/待复核三态，设计 §7.3） */
  export interface CategoryJudgement {
    category: Category;
    categoryLabel: string;
    confidence: number;
    basis: string[];
    status: 'pending_review' | 'primary' | 'secondary';
    contaminationNote?: null | string;
  }

  export interface Recommendation {
    content: string;
    basis: string;
    direction: string;
    priority: number;
  }

  /** 数据门禁结论（设计 §4.3：消费日常质量结论） */
  export interface GateInfo {
    passed: boolean;
    pointCount: number;
    expectedPoints: number;
    validRate: number;
    confidenceLevel: string;
    gapRatio: number;
    reason?: null | string;
  }

  /** 证据波形快照（LTTB ≤2000 点，自包含） */
  export interface ChartSnapshot {
    trend: { ts: number[]; pv?: (null | number)[]; sp?: (null | number)[]; op?: (null | number)[] };
    scatter: { pv: number[]; op: number[] };
  }

  export interface OperatorResult {
    operator: string;
    executed: boolean;
    skipReason?: null | string;
    detected: boolean;
    confidence: number;
    features: Record<string, any>;
    evidence: Array<{ feature: string; value: any; threshold?: any; judgment: string }>;
    error?: null | string;
  }

  /** 记录列表行（列表页） */
  export interface RunListItem {
    id: string;
    taskId?: null | string;
    loopId: string;
    loopTagName?: null | string;
    triggeredBy: string;
    /** 触发类型（MANUAL 手动 / SCHEDULED 分级定时 / EVENT 预警事件） */
    triggerType?: null | TriggerType;
    triggerTypeLabel?: null | string;
    timeWindowStart: string;
    timeWindowEnd: string;
    operatorGroup: string;
    status: RunStatus;
    primaryCategory?: null | Category;
    primaryCategoryLabel?: null | string;
    primaryConfidence?: null | number;
    secondaryCategories: CategoryJudgement[];
    pendingReview: CategoryJudgement[];
    severity?: null | Severity;
    createdAt: string;
  }

  /** 诊断完整详情（结果面板） */
  export interface RunDetail extends RunListItem {
    dataGate: GateInfo;
    operatorResults: Record<string, OperatorResult>;
    fusionResults: Record<
      string,
      { family: string; symptomTag: string; detected: boolean; confidence: number; fused: boolean }
    >;
    symptomTags: Record<string, { detected: boolean; confidence: number }>;
    rationale: string[];
    recommendations: Recommendation[];
    evidenceCharts?: ChartSnapshot;
    thresholdVersion?: null | string;
    algorithmVersion?: null | string;
    startedAt?: null | string;
    finishedAt?: null | string;
    durationMs?: null | number;
    /** 置信度显式定义（分类级 + 算子级 + 融合规则） */
    confidenceDefinitions?: ConfidenceDefinitions;
  }

  /** 算子注册表项（GET /operators，AI 工具目录同源） */
  export interface OperatorInfo {
    name: string;
    displayName: string;
    family: string;
    diagCode: string;
    description: string;
    requiredSignals: string[];
    minSampleRate: number;
    outputsSchema: Record<string, string>;
    thresholdSchema: Record<string, unknown>;
    symptomTags: string[];
    enabledByDefault: boolean;
    fastGroup: boolean;
    /** 置信度计算口径说明 */
    confidenceBasis?: string;
  }

  /** 置信度显式定义（详情 API 附加返回，不入库） */
  export interface ConfidenceDefinitions {
    categories: Record<string, string>;
    operators: Record<string, string>;
    fusion: string;
    secondaryGate: number;
  }

  export type TimeWindowPreset = 'last_24h' | 'last_30d' | 'last_7d';
  export type OperatorGroup = 'fast' | 'full';

  /** 每回路最近诊断概览行（GET /runs/latest，工作台装置节点概览；每回路最多 2 行） */
  export interface LatestRunItem {
    loopId: string;
    loopTagName: string;
    /** 该次诊断记录 ID（null=从未诊断） */
    runId: null | string;
    /** 该回路第几次结论（1=最新, 2=次新；未诊断回路为 null） */
    runSeq?: null | number;
    triggerType?: null | TriggerType;
    triggerTypeLabel?: null | string;
    primaryCategory?: null | Category;
    primaryCategoryLabel?: null | string;
    primaryConfidence?: null | number;
    severity?: null | Severity;
    status?: null | RunStatus;
    lastDiagnosedAt?: null | string;
    timeWindowStart?: null | string;
    timeWindowEnd?: null | string;
  }

  export interface TriggerBody {
    loopIds: string[];
    timeWindow: { preset?: TimeWindowPreset; start?: string; end?: string };
    operatorGroup: OperatorGroup;
    /** 单算子细选白名单（空/缺省=按 operatorGroup 执行） */
    operators?: string[];
  }

  export interface TriggerResult {
    taskId: string;
    accepted: number;
  }

  export interface RunQuery extends PageQuery {
    loopId?: string;
    category?: Category;
    severity?: Severity;
    status?: RunStatus;
    taskId?: string;
    startTime?: string;
    endTime?: string;
  }
}

/**
 * 发起诊断（异步任务，返回 taskId 供轮询）
 */
export function triggerDiagnosisApi(data: DiagnosisApi.TriggerBody) {
  return requestClient.post<DiagnosisApi.TriggerResult>('/diagnosis/run', data);
}

/**
 * 诊断记录列表（筛选/分页）
 */
export function getDiagnosisRunsApi(params: DiagnosisApi.RunQuery) {
  return requestClient.get<PaginatedResponse<DiagnosisApi.RunListItem>>('/diagnosis/runs', {
    params,
  });
}

/** 每回路最新诊断概览（装置节点下钻；无诊断记录的回路 runId=null） */
export function getDiagnosisRunsLatestApi(plantNodeId?: string) {
  return requestClient.get<{ items: DiagnosisApi.LatestRunItem[]; total: number }>(
    '/diagnosis/runs/latest',
    { params: plantNodeId ? { plantNodeId } : undefined },
  );
}

/**
 * 单次诊断完整详情（含算子结果/证据链/波形快照）
 */
export function getDiagnosisRunDetailApi(id: string) {
  return requestClient.get<DiagnosisApi.RunDetail>(`/diagnosis/runs/${id}`);
}

/**
 * 算子注册表元数据（算子说明 + AI 工具目录）
 */
export function getDiagnosisOperatorsApi() {
  return requestClient.get<DiagnosisApi.OperatorInfo[]>('/diagnosis/operators');
}

/**
 * 诊断记录 CSV 导出（返回文本，页面侧构造 Blob 下载）
 */
export function exportDiagnosisRunsApi(params: Omit<DiagnosisApi.RunQuery, 'page' | 'pageSize'>) {
  return requestClient.get<string>('/diagnosis/export', {
    params,
    responseType: 'blob',
  });
}
