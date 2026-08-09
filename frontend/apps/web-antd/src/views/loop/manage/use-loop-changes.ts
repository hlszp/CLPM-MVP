/**
 * 回路变更对比摘要（diff）构建逻辑 — 从 views/loop/manage.vue 拆出
 *
 * 变更确认弹窗的「变更摘要」按上下文分三类：
 * - update：回路基础信息编辑（描述/类型/控制类型/级别/参评/单元/OP 限位/分组）
 * - tagMapping：Tag 关联变更（7 个槽位 已关联/未关联）
 * - batch：批量配置（监控/统计/级别/参评）
 *
 * 同时承载回路域共享的标签映射表（LOOP_TYPE_MAP / CONTROL_TYPE_MAP /
 * IMPORTANCE_LEVEL_TAG / LEVEL_LABEL / SLOT_LABELS），供 manage.vue 表格
 * 与 LoopEditDrawer 复用，避免双份定义漂移。
 */
import type { LoopApi } from '#/api/loop';

/** 变更确认弹窗上下文类型 */
export type ConfirmContextType = 'batch' | 'tagMapping' | 'update';

/** 单条变更摘要 */
export interface DiffEntry {
  field: string;
  from: string;
  to: string;
}

/** OP 输出限位显示（使用默认时恒为「默认」） */
export function formatOpLimit(
  useDefault: boolean,
  value: null | number | undefined,
): string {
  if (useDefault || value === null || value === undefined) return '默认';
  return String(value);
}

/** v5.3：重要等级视觉编码（ZL 语义色 — 1 级 rose / 2 级 amber / 3 级 slate） */
export const IMPORTANCE_LEVEL_TAG: Record<
  number,
  { badgeClass: string; label: string }
> = {
  1: {
    label: '1 级',
    badgeClass:
      'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30',
  },
  2: {
    label: '2 级',
    badgeClass:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
  },
  3: {
    label: '3 级',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
};

export const LOOP_TYPE_MAP: Record<
  string,
  { badgeClass: string; label: string }
> = {
  // 整改 A-02 类别中性化：回路类型为中性分类，统一 slate 灰阶，区分靠文字
  TEMPERATURE: {
    label: '温度',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  PRESSURE: {
    label: '压力',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  LEVEL: {
    label: '液位',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  FLOW: {
    label: '流量',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  ANALYSIS: {
    label: '分析',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  SPEED: {
    label: '速度',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  OTHER: {
    label: '其他',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
};

export const CONTROL_TYPE_MAP: Record<
  string,
  { badgeClass: string; label: string }
> = {
  // 整改 A-02 类别中性化：控制类型为中性分类，统一 slate 灰阶
  STABLE: {
    label: '稳定型',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  SLOW: {
    label: '慢速型',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  FAST: {
    label: '快速型',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
  LOGIC: {
    label: '逻辑型',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
};

export const LEVEL_LABEL: Record<number, string> = {
  1: '1 级',
  2: '2 级',
  3: '3 级',
};

/** Tag 槽位标签（key 为小写槽位名） */
export const SLOT_LABELS: Record<string, string> = {
  pv: 'PV',
  sp: 'SP',
  op: 'OP',
  mode: 'MODE',
  pid_p: 'P',
  pid_i: 'I',
  pid_d: 'D',
};

/** Tag 槽位顺序（diff 与表单展示统一按此顺序） */
export const SLOT_KEYS = [
  'pv',
  'sp',
  'op',
  'mode',
  'pid_p',
  'pid_i',
  'pid_d',
] as const;

export type SlotKey = (typeof SLOT_KEYS)[number];

/** 编辑表单快照（diff 构建所需的最小字段集，与 LoopEditDrawer formState 对齐） */
export interface LoopEditFormSnapshot {
  description?: string;
  unitId?: string;
  loopType?: LoopApi.LoopType | string;
  controlType?: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';
  importanceLevel?: 1 | 2 | 3;
  includeInEvaluation: boolean;
  opOutputLowerLimit?: number;
  opOutputUpperLimit?: number;
  complexLoopGroupId?: string;
  complexRole?: LoopApi.ComplexRole;
  /** 原始分组信息快照（用于判断是否变更） */
  _origComplexLoopGroupId?: string;
  _origComplexRole?: LoopApi.ComplexRole;
}

/**
 * 构建「回路基础信息编辑」变更摘要
 *
 * @param orig 编辑前的回路列表项快照
 * @param form 编辑表单当前值
 * @param opts.useDefaultOpLimits 是否勾选「使用默认 OP 限位」
 * @param opts.unitLabel 单元 ID → 显示标签（工厂节点路径）
 */
export function buildUpdateDiff(
  orig: LoopApi.LoopListItem,
  form: LoopEditFormSnapshot,
  opts: {
    unitLabel: (unitId: string | undefined) => string;
    useDefaultOpLimits: boolean;
  },
): DiffEntry[] {
  const summary: DiffEntry[] = [];
  if ((orig.description ?? '') !== (form.description ?? '')) {
    summary.push({
      field: '回路描述',
      from: orig.description || '—',
      to: form.description || '—',
    });
  }
  const origLoopType = orig.loopType ?? 'OTHER';
  const newLoopType = form.loopType ?? 'OTHER';
  if (origLoopType !== newLoopType) {
    summary.push({
      field: '回路类型',
      from: LOOP_TYPE_MAP[origLoopType]?.label ?? origLoopType,
      to: LOOP_TYPE_MAP[newLoopType]?.label ?? newLoopType,
    });
  }
  if ((orig.controlType ?? undefined) !== (form.controlType ?? undefined)) {
    summary.push({
      field: '控制类型',
      from: orig.controlType
        ? (CONTROL_TYPE_MAP[orig.controlType]?.label ?? orig.controlType)
        : '—',
      to: form.controlType
        ? (CONTROL_TYPE_MAP[form.controlType]?.label ?? form.controlType)
        : '—',
    });
  }
  if (
    (orig.importanceLevel ?? undefined) !== (form.importanceLevel ?? undefined)
  ) {
    summary.push({
      field: '回路级别',
      from: orig.importanceLevel
        ? (LEVEL_LABEL[orig.importanceLevel] ?? String(orig.importanceLevel))
        : '—',
      to: form.importanceLevel
        ? (LEVEL_LABEL[form.importanceLevel] ?? String(form.importanceLevel))
        : '—',
    });
  }
  // v5.3：参评状态变更
  const origEval =
    orig.includeInEvaluation !== false && orig.includeInEvaluation !== null;
  if (origEval !== form.includeInEvaluation) {
    summary.push({
      field: '参评状态',
      from: origEval ? '参评' : '不参评',
      to: form.includeInEvaluation ? '参评' : '不参评',
    });
  }
  if ((orig.unitId ?? undefined) !== (form.unitId ?? undefined)) {
    summary.push({
      field: '所属单元',
      from: opts.unitLabel(orig.unitId ?? undefined),
      to: opts.unitLabel(form.unitId ?? undefined),
    });
  }
  // v6.1：OP 输出限位变更对比
  const origLowerStr =
    orig.opOutputLowerLimit !== null && orig.opOutputLowerLimit !== undefined
      ? String(orig.opOutputLowerLimit)
      : '默认';
  const origUpperStr =
    orig.opOutputUpperLimit !== null && orig.opOutputUpperLimit !== undefined
      ? String(orig.opOutputUpperLimit)
      : '默认';
  const newLowerStr = formatOpLimit(
    opts.useDefaultOpLimits,
    form.opOutputLowerLimit,
  );
  const newUpperStr = formatOpLimit(
    opts.useDefaultOpLimits,
    form.opOutputUpperLimit,
  );
  if (origLowerStr !== newLowerStr || origUpperStr !== newUpperStr) {
    summary.push({
      field: 'OP 输出限位',
      from: `${origLowerStr} ~ ${origUpperStr}`,
      to: `${newLowerStr} ~ ${newUpperStr}`,
    });
  }
  // P4 S4：复杂回路分组变更对比
  const origGroup = form._origComplexLoopGroupId ?? null;
  const origRole = form._origComplexRole ?? null;
  const newGroup = form.complexLoopGroupId ?? null;
  const newRole = form.complexRole ?? null;
  const groupChanged =
    (origGroup ?? null) !== (newGroup ?? null) ||
    (origRole ?? null) !== (newRole ?? null);
  if (groupChanged) {
    const fmt = (gid: null | string, role: null | string) => {
      if (!gid || !role) return '未分组';
      return `${role === 'MAIN' ? '主回路' : '副回路'} ${gid.slice(0, 8)}…`;
    };
    summary.push({
      field: '回路分组',
      from: fmt(origGroup, origRole),
      to: fmt(newGroup, newRole),
    });
  }
  // 评分权重对比已移除（v6.1：回路级权重未参与计算）
  return summary;
}

/**
 * 构建「Tag 关联」变更摘要（7 个槽位 已关联/未关联）
 *
 * @param tagData 变更前的 Tag 关联详情
 * @param slotState 表单当前槽位选择（value=tagId）
 */
export function buildTagMappingDiff(
  tagData: LoopApi.LoopTagsResult,
  slotState: Record<string, string | undefined>,
): DiffEntry[] {
  const summary: DiffEntry[] = [];
  const origMap: Record<string, null | string> = {};
  for (const t of tagData.tags) {
    origMap[t.role.toLowerCase()] = t.tagId;
  }
  for (const key of SLOT_KEYS) {
    const orig = origMap[key] ?? null;
    const now = slotState[key] ?? null;
    if (orig !== now) {
      summary.push({
        field: SLOT_LABELS[key] ?? key,
        from: orig ? '已关联' : '未关联',
        to: now ? '已关联' : '未关联',
      });
    }
  }
  return summary;
}

/**
 * 构建「批量配置」变更摘要（from 恒为「保持原值」）
 */
export function buildBatchDiff(
  batchForm: LoopApi.LoopBatchUpdates,
): DiffEntry[] {
  const summary: DiffEntry[] = [];
  if (batchForm.isMonitored !== undefined) {
    summary.push({
      field: '监控状态',
      from: '保持原值',
      to: batchForm.isMonitored ? '启用监控' : '停用监控',
    });
  }
  if (batchForm.isStatEnabled !== undefined) {
    summary.push({
      field: '统计纳入',
      from: '保持原值',
      to: batchForm.isStatEnabled ? '纳入统计' : '不纳入统计',
    });
  }
  if (batchForm.importanceLevel !== undefined) {
    summary.push({
      field: '回路级别',
      from: '保持原值',
      to:
        LEVEL_LABEL[batchForm.importanceLevel] ??
        String(batchForm.importanceLevel),
    });
  }
  // v5.3：批量参评状态
  if (batchForm.includeInEvaluation !== undefined) {
    summary.push({
      field: '参评状态',
      from: '保持原值',
      to: batchForm.includeInEvaluation ? '参评' : '不参评',
    });
  }
  return summary;
}
