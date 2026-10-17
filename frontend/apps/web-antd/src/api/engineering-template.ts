/**
 * CLPM 工程模板维护 API（P6 Slice M / P2-04 维护 UI 移交子项）
 *
 * 消费后端四组既有能力（P2-04 已验收，本文件为首个前端消费方）：
 * - `/engineering-templates/*`：模板 CRUD / 不可变版本发布 / 指针回拨 / 关联回路清单
 * - `/configs/publish/revision|overrides|overrides/preview|publications|publications/{rev}/rollback`：
 *   P2-02 统一发布链（覆盖预览 dry-run / 真实发布 / 发布账本 / 回退）
 * - `/loops/batch-config`：批量应用（dryRun 逐回路差异 + 部分失败状态；执行模式
 *   仅应用 OK 回路）——**单批 loopIds 上限 200**（后端 schemas 防线），千回路
 *   路径由页面顺序分批（见 views/config/engineering-templates）。
 *
 * 权限口径（后端锚定，前端只做显隐降级）：
 * - 读/预览：`config:view`（ADMIN/IC/PE）；dry-run 预览与读取分离（P2-04）
 * - 模板创建/发布/回拨、覆盖发布/账本回退：`config:publish`（ADMIN，DEC-03）
 * - 批量应用：`loop:batch-config`（ADMIN）
 */
import { requestClient } from '#/api/request';

export namespace EngineeringTemplateApi {
  /** 模板类别：RESPONSE=响应特征 / CONTROL=控制特征 */
  export type TemplateCategory = 'CONTROL' | 'RESPONSE';

  /** 工程模板列表项 */
  export interface TemplateItem {
    id: string;
    templateKey: string;
    name: string;
    category: TemplateCategory;
    /** 当前版本指针（无版本时 null） */
    currentVersion: null | number;
    description: null | string;
    createdBy: null | string;
    createdAt: null | string;
    updatedAt: null | string;
    versionCount?: number;
    loopCount?: number;
  }

  /** 不可变版本行（追加式账本） */
  export interface TemplateVersionItem {
    templateId: string;
    version: number;
    /** 特征档案（键规范随维护 UI 批次冻结，本页按自由 JSON 维护） */
    profile: Record<string, any>;
    reason: null | string;
    publishedBy: null | string;
    publishedAt: null | string;
    /** 固定引用形态 engineering_template:{id}@v{n}（发布新版后不变） */
    revisionRef: string;
  }

  /** 模板详情（含当前版本内容与关联回路样本） */
  export interface TemplateDetail extends TemplateItem {
    currentVersionDetail: null | TemplateVersionItem;
    loops: TemplateLoopItem[];
  }

  /** 模板关联回路项 */
  export interface TemplateLoopItem {
    loopId: string;
    tagName: string;
    isActive: boolean;
  }

  /** 发布/回拨结果（接 P2-02 统一发布链） */
  export interface TemplatePublishResult {
    revision: number;
    /** 本次发布前的 revision（回退引用） */
    rollbackRevision: number;
    templateId: string;
    templateKey: string;
    version: number;
    revisionRef: string;
    affectedLoops: number;
  }

  /** 统一配置 revision 信息 */
  export interface RevisionInfo {
    revision: number;
    lastPublication: null | PublicationItem;
  }

  /** 覆盖预览校验结果 */
  export interface PreviewValidation {
    ok: boolean;
    errors: string[];
  }

  /** 合并视图（逐控制类型：base=该层以下合并，result=叠加本次参数） */
  export interface MergePreviewItem {
    controlType: string;
    base: Record<string, any>;
    result: Record<string, any>;
  }

  /** 覆盖预览（dry-run，零写入）结果 */
  export interface OverridePreviewResult {
    layer: string;
    scopeId: string;
    metricCode: string;
    currentRevision: number;
    /** expectedRevision 失配报告（预览口径，不抛 409） */
    wouldConflict: boolean;
    validation: PreviewValidation;
    before: null | Record<string, any>;
    after: Record<string, any>;
    /** 影响回路数（未注册模板键等场景为 null=诚实降级） */
    affectedLoops: null | number;
    note: null | string;
    affectedLoopSample: TemplateLoopItem[];
    mergePreview: MergePreviewItem[];
  }

  /** 覆盖发布结果 */
  export interface OverridePublishResult {
    revision: number;
    rollbackRevision: number;
    affectedLoops: null | number;
    note: null | string;
    before: null | Record<string, any>;
    after: Record<string, any>;
    layer: string;
    scopeId: string;
    metricCode: string;
  }

  /** 发布账本行 */
  export interface PublicationItem {
    revision: number;
    operation: string;
    layer: null | string;
    scope: Record<string, any>;
    reason: string;
    operator: string;
    beforeValue: null | string;
    afterValue: null | string;
    affectedLoops: null | number;
    rollbackRevision: null | number;
    createdAt: null | string;
  }

  /** 账本回退结果 */
  export interface PublicationRollbackResult {
    revision: number;
    rollbackRevision: number;
    rolledBackRevision: number;
    description: string;
  }

  /** 单字段差异（dry-run/执行结果共用） */
  export interface LoopBatchChangeItem {
    field: string;
    before: unknown;
    after: unknown;
  }

  /** 逐回路结果状态：OK=将更新/已更新；UNCHANGED=无差异；FAIL=回路级校验失败；NOT_FOUND=不存在 */
  export type LoopBatchStatus =
    | 'ALREADY_RESTORED'
    | 'CONFLICT'
    | 'FAIL'
    | 'NOT_FOUND'
    | 'OK'
    | 'UNCHANGED';

  /** 逐回路结果条目（成功/失败项逐条呈现契约） */
  export interface LoopBatchResultItem {
    loopId: string;
    tagName: null | string;
    status: LoopBatchStatus;
    changes: LoopBatchChangeItem[];
    error: null | string;
    beforeState?: null | Record<string, unknown>;
    stateToken?: null | string;
  }

  /** 批量工程属性更新字段（None=不更新该字段；模板版本 pin 须同时给模板 ID） */
  export interface LoopBatchEngineeringUpdates {
    engineeringTemplateId?: null | string;
    engineeringTemplateVersion?: null | number;
    controlActionDirection?: 'DIRECT' | 'REVERSE' | 'UNKNOWN';
    opOutputLowerLimit?: null | number;
    opOutputUpperLimit?: null | number;
  }

  /** 批量应用请求（dryRun=true 预览零写入；loopIds 单批上限 200） */
  export interface LoopBatchRunParams {
    loopIds: string[];
    updates: LoopBatchEngineeringUpdates;
    dryRun?: boolean;
    operationId?: string;
    expectedStateTokens?: Record<string, string>;
  }

  /** 批量应用响应（dryRun/执行同形；results 为逐回路条目） */
  export interface LoopBatchRunResult {
    affected: number;
    action: string;
    loopIds: string[];
    dryRun: boolean;
    results: LoopBatchResultItem[] | null;
    snapshotId?: null | string;
    replayed?: boolean;
  }

  export interface BatchSnapshotSummary {
    snapshotId: string;
    operator: string;
    createdAt: string;
    affected: number;
  }

  export interface BatchSnapshotEntry {
    loopId: string;
    tagName: string;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
    afterToken: string;
  }

  export interface BatchSnapshotDetail {
    snapshotId: string;
    items: BatchSnapshotEntry[];
    total: number;
    offset: number;
    limit: number;
  }
}

// ===========================================================================
// 工程模板 CRUD / 版本 / 发布 / 回拨 / 关联回路
// ===========================================================================

const TEMPLATE_BASE = '/engineering-templates';

/** 模板列表（含版本数与关联回路数） */
export function getEngineeringTemplatesApi() {
  return requestClient.get<{
    items: EngineeringTemplateApi.TemplateItem[];
    total: number;
  }>(TEMPLATE_BASE);
}

/** 创建模板（ADMIN；不发布版本） */
export function createEngineeringTemplateApi(data: {
  category?: EngineeringTemplateApi.TemplateCategory;
  description?: string;
  name: string;
  templateKey: string;
}) {
  return requestClient.post<EngineeringTemplateApi.TemplateItem>(
    TEMPLATE_BASE,
    data,
  );
}

/** 模板详情 + 当前版本 + 关联回路（样本） */
export function getEngineeringTemplateApi(templateId: string) {
  return requestClient.get<EngineeringTemplateApi.TemplateDetail>(
    `${TEMPLATE_BASE}/${templateId}`,
  );
}

/** 版本历史（追加式账本，倒序） */
export function getEngineeringTemplateVersionsApi(templateId: string) {
  return requestClient.get<{
    items: EngineeringTemplateApi.TemplateVersionItem[];
    total: number;
  }>(`${TEMPLATE_BASE}/${templateId}/versions`);
}

/** 发布不可变新版本（ADMIN；expectedRevision 乐观锁，409 冲突） */
export function publishEngineeringTemplateVersionApi(
  templateId: string,
  data: {
    expectedRevision: number;
    profile: Record<string, any>;
    reason: string;
  },
) {
  return requestClient.post<EngineeringTemplateApi.TemplatePublishResult>(
    `${TEMPLATE_BASE}/${templateId}/versions`,
    data,
  );
}

/** 指针回拨到既有版本（ADMIN；版本历史保持追加式） */
export function rollbackEngineeringTemplateVersionApi(
  templateId: string,
  version: number,
  data: { expectedRevision: number; reason: string },
) {
  return requestClient.post<EngineeringTemplateApi.TemplatePublishResult>(
    `${TEMPLATE_BASE}/${templateId}/versions/${version}/rollback`,
    data,
  );
}

/** 模板关联回路清单（limit 上限 200；total 为精确总数） */
export function getEngineeringTemplateLoopsApi(
  templateId: string,
  limit = 200,
) {
  return requestClient.get<{
    items: EngineeringTemplateApi.TemplateLoopItem[];
    total: number;
  }>(`${TEMPLATE_BASE}/${templateId}/loops`, {
    params: { limit },
  });
}

// ===========================================================================
// P2-02 统一发布链：revision / 覆盖预览（dry-run）/ 真实发布 / 账本 / 回退
// ===========================================================================

const PUBLISH_BASE = '/configs/publish';

/** 当前统一配置 revision（expectedRevision 乐观锁取值来源） */
export function getConfigRevisionApi() {
  return requestClient.get<EngineeringTemplateApi.RevisionInfo>(
    `${PUBLISH_BASE}/revision`,
  );
}

/** 覆盖预览（dry-run，只读三角色；零写入，失配报 wouldConflict 不 409） */
export function previewConfigOverrideApi(data: {
  controlType?: string;
  expectedRevision?: number;
  layer: 'LOOP' | 'NODE' | 'TEMPLATE';
  metricCode: string;
  params: Record<string, any>;
  scopeId: string;
}) {
  return requestClient.post<EngineeringTemplateApi.OverridePreviewResult>(
    `${PUBLISH_BASE}/overrides/preview`,
    data,
  );
}

/** 真实发布一层覆盖（ADMIN；整组替换语义；expectedRevision 乐观锁） */
export function publishConfigOverrideApi(data: {
  controlType?: string;
  expectedRevision: number;
  layer: 'LOOP' | 'NODE' | 'TEMPLATE';
  metricCode: string;
  params: Record<string, any>;
  reason: string;
  scopeId: string;
}) {
  return requestClient.post<EngineeringTemplateApi.OverridePublishResult>(
    `${PUBLISH_BASE}/overrides`,
    data,
  );
}

/** 发布账本历史（倒序，limit ≤ 200） */
export function getConfigPublicationsApi(limit = 200) {
  return requestClient.get<{
    items: EngineeringTemplateApi.PublicationItem[];
    total: number;
  }>(`${PUBLISH_BASE}/publications`, { params: { limit } });
}

/** 回退指定发布（ADMIN；before 状态重放为新 revision，不删历史） */
export function rollbackConfigPublicationApi(
  revision: number,
  data: { expectedRevision: number; reason: string },
) {
  return requestClient.post<EngineeringTemplateApi.PublicationRollbackResult>(
    `${PUBLISH_BASE}/publications/${revision}/rollback`,
    data,
  );
}

// ===========================================================================
// 批量应用（dry-run 预览 + 执行；单批 loopIds ≤ 200）
// ===========================================================================

/**
 * 批量配置回路（ADMIN；dryRun=true 零写入预览，执行模式仅应用 OK 回路）
 *
 * 注意：与 api/loop.ts 的 batchConfigLoopsApi 同端点但类型面不同——本绑定
 * 携带 P2-04 工程属性与 dryRun/results 契约（维护 UI 专用），旧绑定仅为
 * 监控/级别字段且未暴露逐回路结果，互不替代。
 */
export function batchApplyLoopsApi(
  data: EngineeringTemplateApi.LoopBatchRunParams,
) {
  return requestClient.post<EngineeringTemplateApi.LoopBatchRunResult>(
    '/loops/batch-config',
    data,
  );
}

/** 本人工程批次历史；可在重开页面后继续恢复，total 为完整分母。 */
export function getLoopBatchSnapshotsApi(offset = 0, limit = 20) {
  return requestClient.get<{
    items: EngineeringTemplateApi.BatchSnapshotSummary[];
    total: number;
  }>('/loops/batch-snapshots', { params: { offset, limit } });
}

export function getLoopBatchSnapshotApi(
  snapshotId: string,
  offset = 0,
  limit = 200,
) {
  return requestClient.get<EngineeringTemplateApi.BatchSnapshotDetail>(
    `/loops/batch-snapshots/${snapshotId}`,
    { params: { offset, limit } },
  );
}

/** 恢复服务端快照原值（含 NULL）；真实恢复前必须重新 dry-run。 */
export function restoreLoopBatchSnapshotApi(
  snapshotId: string,
  data: {
    dryRun: boolean;
    loopIds?: string[];
    reason: string;
  },
) {
  return requestClient.post<EngineeringTemplateApi.LoopBatchRunResult>(
    `/loops/batch-snapshots/${snapshotId}/restore`,
    data,
  );
}
