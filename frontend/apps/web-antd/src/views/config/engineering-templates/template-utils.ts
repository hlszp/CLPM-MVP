/**
 * 工程模板维护页纯函数（P6 Slice M）
 *
 * 与组件解耦的可单测逻辑：
 * - profile 版本差异计算（版本详情与差异 / 发布预览「与当前版本差异」）
 * - 发布流程状态机（dry-run → 确认 → 真实发布 → 结果；模板版本发布 /
 *   覆盖发布 / 批量应用三向导共用）
 * - 批量回路分批（后端单批 loopIds ≤ 200 的客户端顺序分批）
 * - 批量结果汇总与状态标签（成功/失败项逐条呈现）
 * - 诚实化口径：清单截断提示（仅显示前 N 条（共 M 条））
 */

// ===========================================================================
// profile 版本差异
// ===========================================================================

export type ProfileDiffKind = 'added' | 'changed' | 'removed' | 'unchanged';

export interface ProfileDiffEntry {
  key: string;
  kind: ProfileDiffKind;
  before: unknown;
  after: unknown;
}

/** 值相等判定（JSON 序列化比较；profile 为服务端 JSONB 自由档案） */
function profileValueEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

/**
 * 计算两版本 profile 的键级差异（键并集按字典序排列）。
 *
 * before=null（首版发布/无基线）时全部键为 added；after=null 时全部 removed。
 */
export function diffProfileEntries(
  before: null | Record<string, any> | undefined,
  after: null | Record<string, any> | undefined,
): ProfileDiffEntry[] {
  const b = before ?? {};
  const a = after ?? {};
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])].toSorted();
  return keys.map((key) => {
    const inBefore = Object.prototype.hasOwnProperty.call(b, key);
    const inAfter = Object.prototype.hasOwnProperty.call(a, key);
    if (!inBefore)
      return { key, kind: 'added', before: undefined, after: a[key] };
    if (!inAfter)
      return { key, kind: 'removed', before: b[key], after: undefined };
    return profileValueEqual(b[key], a[key])
      ? { key, kind: 'unchanged', before: b[key], after: a[key] }
      : { key, kind: 'changed', before: b[key], after: a[key] };
  });
}

/** 非 unchanged 的差异条数（「与当前版本相比 N 项变化」口径） */
export function countProfileChanges(entries: ProfileDiffEntry[]): number {
  return entries.filter((e) => e.kind !== 'unchanged').length;
}

export const PROFILE_DIFF_LABEL: Record<ProfileDiffKind, string> = {
  added: '新增',
  removed: '移除',
  changed: '变更',
  unchanged: '不变',
};

// ===========================================================================
// 发布流程状态机（dry-run → 确认 → 真实发布 → 结果）
// ===========================================================================

/**
 * 状态：
 * - edit：编辑参数/档案
 * - previewing：dry-run 预览请求中
 * - previewed：预览就绪（校验/差异/影响范围可见），可返回编辑或确认发布
 * - publishing：真实发布请求中（不可再编辑）
 * - done：发布成功（展示 revision/revisionRef/affectedLoops 与回退引用）
 * - failed：发布失败（含 409 冲突；可重新预览或重试）
 */
export type PublishFlowStep =
  | 'done'
  | 'edit'
  | 'failed'
  | 'previewed'
  | 'previewing'
  | 'publishing';

export type PublishFlowEvent =
  | 'BACK_TO_EDIT'
  | 'CONFIRM_PUBLISH'
  | 'PREVIEW_ERROR'
  | 'PREVIEW_OK'
  | 'PUBLISH_ERROR'
  | 'PUBLISH_OK'
  | 'RESET'
  | 'START_PREVIEW';

const FLOW_TRANSITIONS: Record<
  PublishFlowStep,
  Partial<Record<PublishFlowEvent, PublishFlowStep>>
> = {
  edit: { START_PREVIEW: 'previewing' },
  previewing: { PREVIEW_OK: 'previewed', PREVIEW_ERROR: 'edit' },
  previewed: {
    BACK_TO_EDIT: 'edit',
    START_PREVIEW: 'previewing',
    CONFIRM_PUBLISH: 'publishing',
  },
  publishing: { PUBLISH_OK: 'done', PUBLISH_ERROR: 'failed' },
  failed: {
    BACK_TO_EDIT: 'edit',
    START_PREVIEW: 'previewing',
    CONFIRM_PUBLISH: 'publishing',
  },
  done: { RESET: 'edit' },
};

/**
 * 状态迁移（非法迁移保持原态——调用方据此判断按钮可用性，
 * 保证「未预览不得发布」这一硬约束在 UI 层不可绕过）。
 */
export function reducePublishFlow(
  step: PublishFlowStep,
  event: PublishFlowEvent,
): PublishFlowStep {
  return FLOW_TRANSITIONS[step][event] ?? step;
}

/** 是否可发起 dry-run 预览 */
export function canPreview(step: PublishFlowStep): boolean {
  return step === 'edit' || step === 'previewed' || step === 'failed';
}

/** 是否可确认真实发布（必须先完成 dry-run 预览） */
export function canConfirmPublish(step: PublishFlowStep): boolean {
  return step === 'previewed' || step === 'failed';
}

// ===========================================================================
// 批量应用：分批 / 汇总 / 状态呈现
// ===========================================================================

/** 后端单批 loopIds 上限（schemas/loop_batch.py max_length=200 防线） */
export const BATCH_LOOP_IDS_LIMIT = 200;

/**
 * 千回路路径：客户端顺序分批（每批 ≤ 200，逐批 dry-run → 确认 → 执行）。
 * 容量实证归 P5-05/G5-D02（本页显式提示该口径，不虚称已验证）。
 */
export function chunkLoopIds(
  loopIds: string[],
  size = BATCH_LOOP_IDS_LIMIT,
): string[][] {
  const safeSize = Math.max(1, Math.min(size, BATCH_LOOP_IDS_LIMIT));
  const chunks: string[][] = [];
  for (let i = 0; i < loopIds.length; i += safeSize) {
    chunks.push(loopIds.slice(i, i + safeSize));
  }
  return chunks;
}

export interface BatchSummary {
  total: number;
  ok: number;
  unchanged: number;
  failed: number;
  notFound: number;
  /** 实际应用/将应用数（OK 条目数，与后端 affected 同口径） */
  affected: number;
}

/** 逐条结果汇总（成功/失败项计数；跨分批合并口径） */
export function summarizeBatchResults(
  items: {
    status:
      | 'ALREADY_RESTORED'
      | 'CONFLICT'
      | 'FAIL'
      | 'NOT_FOUND'
      | 'OK'
      | 'UNCHANGED';
  }[],
): BatchSummary {
  const summary: BatchSummary = {
    total: items.length,
    ok: 0,
    unchanged: 0,
    failed: 0,
    notFound: 0,
    affected: 0,
  };
  for (const item of items) {
    switch (item.status) {
      case 'ALREADY_RESTORED':
      case 'UNCHANGED': {
        summary.unchanged += 1;

        break;
      }
      case 'CONFLICT':
      case 'FAIL': {
        summary.failed += 1;

        break;
      }
      case 'NOT_FOUND': {
        summary.notFound += 1;

        break;
      }
      case 'OK': {
        summary.ok += 1;
        summary.affected += 1;

        break;
      }
      // No default
    }
  }
  return summary;
}

export const BATCH_STATUS_LABEL: Record<
  'ALREADY_RESTORED' | 'CONFLICT' | 'FAIL' | 'NOT_FOUND' | 'OK' | 'UNCHANGED',
  string
> = {
  OK: '成功',
  UNCHANGED: '无变化',
  FAIL: '失败',
  NOT_FOUND: '不存在',
  CONFLICT: '并发冲突（未写入）',
  ALREADY_RESTORED: '已恢复（幂等跳过）',
};

// ===========================================================================
// 展示辅助（诚实化口径）
// ===========================================================================

export const TEMPLATE_CATEGORY_LABEL: Record<string, string> = {
  RESPONSE: '响应特征',
  CONTROL: '控制特征',
};

/**
 * 清单截断提示（诚实化原则：禁止静默截断）。
 * total > 已加载条数时必须显式提示「仅显示前 N 条（共 M 条）」。
 */
export function truncationNote(loaded: number, total: number): null | string {
  if (total > loaded) {
    return `仅显示前 ${loaded} 条（共 ${total} 条）`;
  }
  return null;
}

/** 解析 profile JSON 文本（须为顶层对象；数组/标量/语法错误均拒绝） */
export function parseProfileJson(text: string): {
  error?: string;
  ok: boolean;
  value?: Record<string, any>;
} {
  const trimmed = text.trim();
  if (!trimmed) {
    return { ok: false, error: 'profile 不能为空（无内容时请填 {}）' };
  }
  try {
    const value = JSON.parse(trimmed);
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return {
        ok: false,
        error: 'profile 必须是 JSON 对象（键值对），不支持数组或标量',
      };
    }
    return { ok: true, value: value as Record<string, any> };
  } catch (error) {
    return { ok: false, error: `JSON 语法错误：${(error as Error).message}` };
  }
}
