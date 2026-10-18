/**
 * 我的工作与个人关系 API（P6-06 O06-NAV）
 *
 * 后端契约（树内源码即事实）：
 * - `backend/app/api/v1/endpoints/personal_work.py`：GET /me/work 跨来源统一
 *   工作查询（ASSIGNED/SYSTEM_SUGGESTION/FOLLOW/RESUME 四 workKind；
 *   等待→今日计划→现在可行动互斥分区；来源失败 sourceStatuses 如实 ERROR、
 *   total=None 不填伪造 0；cursor 稳定分页）
 * - `backend/app/api/v1/endpoints/personal_workspace.py`：/me 六端点
 *   （loop-follows / workspace-preferences / work-resume），写操作均为
 *   设定语义：expectedRevision CAS + requestId 幂等 + generation 清空栅栏；
 *   过期版本 409 携 currentRevision/currentGeneration
 * - `backend/app/api/v1/endpoints/loop_investigation.py`：核查 claim/handoff
 *   等动作（409 携 currentRevision/stage/ownerId）
 *
 * 客户端纪律（01a §2.1/§3 + 03 号文）：
 * - 只在服务端确认后显示已保存；断网显示未保存，不造离线成功；
 * - 预期冲突（409 刷新重试）的请求带 skipErrorMessage，由调用方处理，
 *   不弹全局错误提示；
 * - 撤权对象只显示 inaccessibleCount 计数，不显示名称/状态/证据；
 * - 空待办 ≠ 健康：本模块不输出任何健康断言。
 */

import { requestClient } from '#/api/request';

export namespace PersonalWorkApi {
  /* ── 统一工作查询（GET /me/work） ── */

  export type WorkKind = 'ASSIGNED' | 'FOLLOW' | 'RESUME' | 'SYSTEM_SUGGESTION';

  /** 工作分区（互斥；FUTURE/CLOSED 仅经筛选查询） */
  export type Partition =
    | 'ACTIONABLE'
    | 'CLOSED'
    | 'FUTURE'
    | 'TODAY'
    | 'WAITING';

  /** 集合完整性 */
  export type ReadStatus = 'COMPLETE' | 'ERROR' | 'PARTIAL';

  export interface EvidenceRef {
    id: string;
    kind: string;
    version?: null | string;
  }

  export interface NextAction {
    kind: string;
    label: string;
  }

  /** 统一工作项（字段对齐 schemas/personal_work.PersonalWorkItem） */
  export interface WorkItem {
    workKind: WorkKind;
    workId: string;
    loopId: string;
    title: string;
    reason: string;
    sourceKind: string;
    ownerId?: null | string;
    ownerDisplayName?: null | string;
    responsibilityPending: boolean;
    stage?: null | string;
    partition?: null | Partition;
    plannedAt?: null | string;
    dueAt?: null | string;
    waitReason?: null | string;
    waitReviewCondition?: null | string;
    priorityReasons: string[];
    evidenceRefs: EvidenceRef[];
    nextAction: NextAction;
    revision?: null | number;
    loopTagName?: null | string;
    unitId?: null | string;
    unitName?: null | string;
    context?: null | Record<string, any>;
    updatedAt?: null | string;
  }

  /** 单来源读取状态（失败如实 ERROR；total 未知为 null，不填 0） */
  export interface WorkSourceStatus {
    status: 'ERROR' | 'OK';
    total?: null | number;
    inaccessibleCount: number;
  }

  /** GET /me/work 响应数据 */
  export interface WorkData {
    items: WorkItem[];
    /** 与分页同规则的计数；任一来源失败 → null（计数未知，非 0） */
    total?: null | number;
    nextCursor?: null | string;
    readAt: string;
    readStatus: ReadStatus;
    sourceStatuses: Record<string, WorkSourceStatus>;
  }

  export interface WorkQuery {
    workKind?: WorkKind;
    partition?: Partition;
    cursor?: string;
    pageSize?: number;
  }

  /* ── 回路关注（/me/loop-follows） ── */

  export interface LoopFollowItem {
    loopId: string;
    tagName: string;
    description?: null | string;
    unitId?: null | string;
    isFollowed: boolean;
    revision: number;
    updatedAt: string;
  }

  export interface LoopFollowListData {
    items: LoopFollowItem[];
    total: number;
    page: number;
    pageSize: number;
    inaccessibleCount: number;
    readStatus: ReadStatus;
    readAt: string;
  }

  export interface LoopFollowSetResult {
    loopId: string;
    isFollowed: boolean;
    revision: number;
    updatedAt: string;
    replayed: boolean;
  }

  /* ── 账号工作偏好（/me/workspace-preferences） ── */

  export interface PreferenceItem {
    payload: Record<string, any>;
    schemaVersion: number;
    revision: number;
    updatedAt: string;
  }

  export interface PreferenceListData {
    items: Record<string, PreferenceItem>;
    readAt: string;
  }

  export interface PreferencePutResult {
    preferenceKey: string;
    payload: Record<string, any>;
    schemaVersion: number;
    revision: number;
    updatedAt: string;
    replayed: boolean;
  }

  /* ── 最近继续（/me/work-resume） ── */

  export interface WorkResumeItem {
    resourceKind: string;
    resourceId: string;
    context: Record<string, any>;
    revision: number;
    generation: number;
    lastVisitedAt: string;
  }

  export interface WorkResumeListData {
    items: WorkResumeItem[];
    currentGeneration: number;
    inaccessibleCount: number;
    readStatus: ReadStatus;
    readAt: string;
  }

  export interface WorkResumePutResult {
    resourceKind: string;
    resourceId: string;
    revision: number;
    generation: number;
    lastVisitedAt: string;
    replayed: boolean;
  }

  export interface WorkResumeDeleteResult {
    resourceKind: string;
    resourceId: string;
    deleted: boolean;
    revision: number;
    currentGeneration: number;
  }

  export interface WorkResumeClearResult {
    currentGeneration: number;
    clearedCount: number;
  }

  /* ── 核查（/investigations 动作；claim/handoff 409 刷新重试） ── */

  export interface InvestigationView {
    id: string;
    loopId: string;
    sourceKind: string;
    sourceId: string;
    problemKey: string;
    title: string;
    detail?: null | string;
    stage: string;
    outcome?: null | string;
    outcomeNote?: null | string;
    ownerId?: null | string;
    ownerDisplayName?: null | string;
    responsibilityPending: boolean;
    waitReason?: null | string;
    waitReviewCondition?: null | string;
    waitResponsible?: null | string;
    closeTargetKind?: null | string;
    closeTargetId?: null | string;
    handoffSourceId?: null | string;
    revision: number;
    createdBy?: null | string;
    closedBy?: null | string;
    closedAt?: null | string;
    createdAt: string;
    updatedAt: string;
  }

  export interface InvestigationResult {
    item: InvestigationView;
    replayed: boolean;
  }

  export interface InvestigationHandoffResult {
    source: InvestigationView;
    target: InvestigationView;
    replayed: boolean;
  }
}

/* ── 409 冲突提取（刷新重试协议） ── */

/**
 * 版本/代际冲突载荷（从请求错误体提取）。
 * 后端错误体统一为 {code, message, data}；409 时 code 为
 * ERR_REVISION_CONFLICT / ERR_GENERATION_STALE，data 携带当前有效版本。
 */
export interface RevisionConflict {
  code: string;
  message: string;
  currentRevision?: null | number;
  currentGeneration?: null | number;
  /** 核查 409 附带：当前阶段/负责人（败方据此显示"已被认领"而非盲目重试） */
  stage?: null | string;
  ownerId?: null | string;
  isFollowed?: boolean | null;
}

/**
 * RequestClient.request 已将 HTTP 错误解包为后端 body，按明确的冲突码识别。
 * 同时兼容仍保留 Axios response 的调用方；该形状必须是 HTTP 409。
 */
export function extractRevisionConflict(
  error: unknown,
): null | RevisionConflict {
  if (!error || typeof error !== 'object') return null;
  const response = (error as any)?.response;
  if (response && response.status !== 409) return null;
  const body = response ? response.data : error;
  if (!body || typeof body !== 'object') return null;
  const code = (body as Record<string, any>).code;
  if (code !== 'ERR_REVISION_CONFLICT' && code !== 'ERR_GENERATION_STALE') {
    return null;
  }
  const data = (body as Record<string, any>).data ?? {};
  return {
    code,
    message: String(
      (body as Record<string, any>).message ?? '版本冲突，请刷新后重试',
    ),
    currentRevision:
      typeof data.currentRevision === 'number' ? data.currentRevision : null,
    currentGeneration:
      typeof data.currentGeneration === 'number'
        ? data.currentGeneration
        : null,
    stage: typeof data.stage === 'string' ? data.stage : null,
    ownerId: typeof data.ownerId === 'string' ? data.ownerId : null,
    isFollowed: typeof data.isFollowed === 'boolean' ? data.isFollowed : null,
  };
}

/** 请求身份（requestId）：CAS+幂等协议要求，单次操作内稳定复用 */
export function newRequestId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID();
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

/* ── 统一工作查询 ── */

export function getMyWorkApi(params: PersonalWorkApi.WorkQuery = {}) {
  return requestClient.get<PersonalWorkApi.WorkData>('/me/work', { params });
}

/* ── 回路关注 ── */

export function getMyLoopFollowsApi(params: {
  page?: number;
  pageSize?: number;
  search?: string;
}) {
  return requestClient.get<PersonalWorkApi.LoopFollowListData>(
    '/me/loop-follows',
    { params },
  );
}

/** 设定关注状态（设定语义非 toggle；409 由调用方刷新重试，不弹全局提示） */
export function setLoopFollowApi(
  loopId: string,
  body: { expectedRevision: number; isFollowed: boolean; requestId: string },
) {
  return requestClient.put<PersonalWorkApi.LoopFollowSetResult>(
    `/me/loop-follows/${loopId}`,
    body,
    { skipErrorMessage: true } as any,
  );
}

/* ── 账号工作偏好 ── */

export function getWorkspacePreferencesApi() {
  return requestClient.get<PersonalWorkApi.PreferenceListData>(
    '/me/workspace-preferences',
  );
}

/** 按单 key 设定偏好（409 由调用方刷新重试，不弹全局提示） */
export function putWorkspacePreferenceApi(
  key: string,
  body: {
    expectedRevision: number;
    payload: Record<string, any>;
    requestId: string;
  },
) {
  return requestClient.put<PersonalWorkApi.PreferencePutResult>(
    `/me/workspace-preferences/${key}`,
    body,
    { skipErrorMessage: true } as any,
  );
}

/* ── 最近继续 ── */

export function getWorkResumeApi(params?: { kind?: string; limit?: number }) {
  return requestClient.get<PersonalWorkApi.WorkResumeListData>(
    '/me/work-resume',
    { params },
  );
}

/** 记录最近继续（409 由调用方刷新重试，不弹全局提示） */
export function putWorkResumeApi(
  kind: string,
  resourceId: string,
  body: {
    context: Record<string, any>;
    expectedRevision: number;
    generation: number;
    requestId: string;
  },
) {
  return requestClient.put<PersonalWorkApi.WorkResumePutResult>(
    `/me/work-resume/${kind}/${resourceId}`,
    body,
    { skipErrorMessage: true } as any,
  );
}

/** 删除单条最近继续（保留版本墓碑；409 由调用方刷新重试） */
export function deleteWorkResumeApi(
  kind: string,
  resourceId: string,
  generation: number,
) {
  return requestClient.delete<PersonalWorkApi.WorkResumeDeleteResult>(
    `/me/work-resume/${kind}/${resourceId}`,
    { params: { generation }, skipErrorMessage: true } as any,
  );
}

/** 清空本人最近继续（先原子增代际再清旧代） */
export function clearWorkResumeApi() {
  return requestClient.delete<PersonalWorkApi.WorkResumeClearResult>(
    '/me/work-resume',
    { skipErrorMessage: true } as any,
  );
}

/* ── 核查动作（claim/handoff：409 由调用方刷新重试） ── */

export function getInvestigationApi(investigationId: string) {
  return requestClient.get<PersonalWorkApi.InvestigationView>(
    `/investigations/${investigationId}`,
    { skipErrorMessage: true } as any,
  );
}

/** 认领核查（并发认领 CAS；败方 409 携当前版本/阶段/负责人） */
export function claimInvestigationApi(
  investigationId: string,
  body: { expectedRevision: number; requestId: string },
) {
  return requestClient.post<PersonalWorkApi.InvestigationResult>(
    `/investigations/${investigationId}/claim`,
    body,
    { skipErrorMessage: true } as any,
  );
}

/** 移交核查（双记录原子；接手人只接受稳定 sys_user.id） */
export function handoffInvestigationApi(
  investigationId: string,
  body: {
    expectedRevision: number;
    newOwnerId: string;
    note?: null | string;
    requestId: string;
  },
) {
  return requestClient.post<PersonalWorkApi.InvestigationHandoffResult>(
    `/investigations/${investigationId}/handoff`,
    body,
    { skipErrorMessage: true } as any,
  );
}
