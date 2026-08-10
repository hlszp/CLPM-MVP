/**
 * 统一监控上下文 composable（MW-P1-01）
 *
 * URL 是监控上下文真相源；所有筛选、回路选择、时间窗、深链接上下文
 * 统一从 query 读取并通过 router.replace 更新，不新增标签页/面包屑。
 *
 * 覆盖字段：
 * - view: 'workspace' | 'table'（工作台 / 批量表格模式）
 * - loopId: 当前选中回路
 * - plantNodeId: 装置/单元筛选
 * - loopType: 回路类型筛选
 * - keyword: 搜索关键词
 * - attentionOnly: 只看关注项（Phase 2 API 就绪后启用）
 * - timeWindow: 8h/12h/24h/48h/72h 五档
 * - eventId: 预警事件深链接
 * - trackerId: Tracker 深链接
 * - section: 工作台区锚点
 *
 * 对齐整改方案 §9.1。
 */
import type { Router } from 'vue-router';

import { computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

/** 监控视图模式 */
export type MonitorView = 'table' | 'workspace';

/** 监控时间窗五档（保留既有 8h/12h/24h/48h/72h） */
export type MonitorTimeWindow = '8h' | '12h' | '24h' | '48h' | '72h';

/** 工作台区锚点 */
export type WorkbenchSection =
  | 'assessment'
  | 'diagnosis'
  | 'overview'
  | 'tuning'
  | 'verification';

/** 监控上下文完整状态 */
export interface MonitorContext {
  attentionOnly: boolean;
  eventId: null | string;
  keyword: string;
  loopId: null | string;
  loopType: null | string;
  plantNodeId: null | string;
  section: null | WorkbenchSection;
  timeWindow: MonitorTimeWindow;
  trackerId: null | string;
  view: MonitorView;
}

/** URL query 白名单键 */
const CONTEXT_KEYS = [
  'view',
  'loopId',
  'plantNodeId',
  'loopType',
  'keyword',
  'attentionOnly',
  'timeWindow',
  'eventId',
  'trackerId',
  'section',
] as const;

/** 合法时间窗集合 */
const VALID_TIME_WINDOWS = new Set<MonitorTimeWindow>([
  '8h',
  '12h',
  '24h',
  '48h',
  '72h',
]);

/** 合法视图模式集合 */
const VALID_VIEWS = new Set<MonitorView>(['table', 'workspace']);

/** 合法区锚点集合 */
const VALID_SECTIONS = new Set<WorkbenchSection>([
  'assessment',
  'diagnosis',
  'overview',
  'tuning',
  'verification',
]);

/** 解析字符串 query 值，空/未定义返回 null */
function parseStr(v: unknown): null | string {
  if (typeof v !== 'string') return null;
  const trimmed = v.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** 解析 keyword（允许空字符串，不返回 null） */
function parseKeyword(v: unknown): string {
  if (typeof v !== 'string') return '';
  return v.trim();
}

/** 解析布尔值（attentionOnly=1/true → true） */
function parseBool(v: unknown): boolean {
  return v === '1' || v === 'true';
}

/** 解析时间窗，非法值回退到默认 24h */
function parseTimeWindow(v: unknown): MonitorTimeWindow {
  const parsed = parseStr(v);
  if (parsed && VALID_TIME_WINDOWS.has(parsed as MonitorTimeWindow)) {
    return parsed as MonitorTimeWindow;
  }
  return '24h';
}

/** 解析视图模式，非法值回退到 workspace */
function parseView(v: unknown): MonitorView {
  const parsed = parseStr(v);
  if (parsed && VALID_VIEWS.has(parsed as MonitorView)) {
    return parsed as MonitorView;
  }
  return 'workspace';
}

/** 解析区锚点，非法值返回 null */
function parseSection(v: unknown): null | WorkbenchSection {
  const parsed = parseStr(v);
  if (parsed && VALID_SECTIONS.has(parsed as WorkbenchSection)) {
    return parsed as WorkbenchSection;
  }
  return null;
}

/**
 * 读取当前路由的完整监控上下文。
 *
 * 用法：
 * ```ts
 * const ctx = useMonitorContext();
 * ctx.update({ loopId: 'abc' });      // router.replace，不新增 tab
 * ctx.reset({ view: 'workspace' });   // 清空除 view 外所有字段
 * ```
 */
export function useMonitorContext() {
  const route = useRoute();
  const router = useRouter();

  const context = computed<MonitorContext>(() => ({
    attentionOnly: parseBool(route.query.attentionOnly),
    eventId: parseStr(route.query.eventId),
    keyword: parseKeyword(route.query.keyword),
    loopId: parseStr(route.query.loopId),
    loopType: parseStr(route.query.loopType),
    plantNodeId: parseStr(route.query.plantNodeId),
    section: parseSection(route.query.section),
    timeWindow: parseTimeWindow(route.query.timeWindow),
    trackerId: parseStr(route.query.trackerId),
    view: parseView(route.query.view),
  }));

  /** 便捷读取 */
  const view = computed(() => context.value.view);
  const loopId = computed(() => context.value.loopId);
  const plantNodeId = computed(() => context.value.plantNodeId);
  const loopType = computed(() => context.value.loopType);
  const keyword = computed(() => context.value.keyword);
  const attentionOnly = computed(() => context.value.attentionOnly);
  const timeWindow = computed(() => context.value.timeWindow);
  const eventId = computed(() => context.value.eventId);
  const trackerId = computed(() => context.value.trackerId);
  const section = computed(() => context.value.section);

  /**
   * 增量更新上下文（合并到现有 query，未传字段保留原值）。
   * 使用 router.replace 避免新增 tab/面包屑。
   * 传 null 表示清除该字段。
   */
  function update(patch: Partial<MonitorContext>): void {
    const current = context.value;
    const currentRecord = current as unknown as Record<string, unknown>;
    const patchRecord = patch as unknown as Record<string, unknown>;

    // 合并现有白名单字段和 patch，跳过 null/空/false 值
    const nextQuery: Record<string, string> = {};
    for (const key of CONTEXT_KEYS) {
      const patchVal = patchRecord[key];
      // patch 中明确指定的值优先（包括 null 表示清除）
      const effectiveVal = key in patch ? patchVal : currentRecord[key];
      if (
        effectiveVal !== null &&
        effectiveVal !== '' &&
        effectiveVal !== false
      ) {
        nextQuery[key] = String(effectiveVal);
      }
    }

    router.replace({ query: nextQuery });
  }

  /**
   * 重置上下文（保留 seed 中指定的字段，其余清空）。
   * 典型用法：reset({ view: 'workspace' }) 清空所有筛选但保持工作台模式。
   */
  function reset(seed: Partial<MonitorContext> = {}): void {
    const nextQuery: Record<string, string> = {};
    for (const [key, val] of Object.entries(seed)) {
      if (val !== null && val !== '' && val !== false) {
        nextQuery[key] = String(val);
      }
    }
    router.replace({ query: nextQuery });
  }

  /**
   * 携带当前监控上下文跳转到目标路径。
   * 保留 loopId/timeWindow/eventId/trackerId 等已知上下文，不默认丢弃。
   */
  function navigateWithMonitorContext(
    target: string,
    extra: Record<string, string> = {},
  ): void {
    const current = context.value;
    const carry: Record<string, string> = {};
    if (current.loopId) carry.loopId = current.loopId;
    if (current.timeWindow) carry.timeWindow = current.timeWindow;
    if (current.eventId) carry.eventId = current.eventId;
    if (current.trackerId) carry.trackerId = current.trackerId;
    router.push({ path: target, query: { ...carry, ...extra } });
  }

  return {
    // 完整上下文
    context,
    // 便捷读取
    attentionOnly,
    eventId,
    keyword,
    loopId,
    loopType,
    plantNodeId,
    section,
    timeWindow,
    trackerId,
    view,
    // 操作
    navigateWithMonitorContext,
    reset,
    update,
  };
}

/**
 * 监听上下文中指定字段的变化（用于触发数据重新加载）。
 *
 * ```ts
 * watchMonitorField('loopId', (newId) => loadDetail(newId));
 * watchMonitorField(['plantNodeId', 'loopType', 'keyword'], () => reloadList());
 * ```
 */
export function watchMonitorField(
  fields: string | string[],
  cb: () => void,
  router?: Router,
): void {
  const { context } = useMonitorContext();
  const fieldList = Array.isArray(fields) ? fields : [fields];
  const ctxRecord = context as unknown as { value: Record<string, unknown> };
  watch(() => fieldList.map((f) => ctxRecord.value[f]), cb, { deep: true });
  void router; // 预留签名兼容
}
