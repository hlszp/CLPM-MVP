import type { FilterPreset } from './use-clpm-preferences';

/**
 * 保存视图 composable（MW-P4-03 统一筛选与保存视图）
 *
 * 保存视图包含模式、筛选和时间窗，不包含 eventId/trackerId/section（深链接上下文）。
 * 应用保存视图时无权限字段被安全忽略：EXPERT/SPONSOR 不能使用 table 模式，
 * 应用 view=table 时回退到 workspace。
 *
 * 对齐整改方案 §9.4。
 */
import type { MonitorContext } from '#/composables/use-monitor-context';

import { computed } from 'vue';

import { useUserStore } from '@vben/stores';

import { usePagePreference } from './use-clpm-preferences';
import { useMonitorContext } from './use-monitor-context';

/** 保存视图包含的字段（不包含 eventId/trackerId/section） */
export const SAVED_VIEW_FIELDS = [
  'view',
  'timeWindow',
  'plantNodeId',
  'loopType',
  'keyword',
  'attentionOnly',
] as const;

/** table 模式可用角色（EXPERT/SPONSOR 不可用） */
const TABLE_VIEW_ROLES = new Set(['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']);

/**
 * 检查当前用户是否有权使用 table 模式。
 * EXPERT 强制 workspace，SPONSOR 不开放工作台路由。
 */
export function canUseTableViewByRoles(roles: string[]): boolean {
  return roles.some((r) => TABLE_VIEW_ROLES.has(r));
}

/**
 * 构建保存视图的 filters 对象。
 * 只包含 SAVED_VIEW_FIELDS，排除 eventId/trackerId/section。
 */
export function buildSavedViewFilters(
  ctx: MonitorContext,
): Record<string, any> {
  return {
    view: ctx.view,
    timeWindow: ctx.timeWindow,
    plantNodeId: ctx.plantNodeId,
    loopType: ctx.loopType,
    keyword: ctx.keyword,
    attentionOnly: ctx.attentionOnly,
  };
}

/**
 * 从预设构建应用 patch，执行权限安全过滤。
 * - view=table 对无权限角色回退为 workspace
 * - 明确清除 eventId/trackerId/section（保存视图不携带深链接上下文）
 */
export function buildApplyPatch(
  preset: FilterPreset,
  roles: string[],
): Partial<MonitorContext> {
  const f = preset.filters;
  const patch: Partial<MonitorContext> = {};

  // 模式：权限安全的视图应用
  if (f.view !== undefined) {
    patch.view =
      f.view === 'table' && !canUseTableViewByRoles(roles)
        ? 'workspace'
        : f.view;
  }
  if (f.timeWindow !== undefined) patch.timeWindow = f.timeWindow;
  if (f.plantNodeId !== undefined) patch.plantNodeId = f.plantNodeId || null;
  if (f.loopType !== undefined) patch.loopType = f.loopType || null;
  if (f.keyword !== undefined) patch.keyword = f.keyword;
  if (f.attentionOnly !== undefined) {
    patch.attentionOnly = !!f.attentionOnly;
  }
  // 明确清除深链接上下文——保存视图不携带 eventId/trackerId/section
  patch.eventId = null;
  patch.trackerId = null;
  patch.section = null;

  return patch;
}

/**
 * 保存视图 composable。
 *
 * 用法：
 * ```ts
 * const { savedFilters, saveCurrentView, applyView } = useSavedView('monitor-workbench');
 * saveCurrentView('我的预设');
 * applyView('preset-id');
 * ```
 */
export function useSavedView(pageKey: string) {
  const monitorCtx = useMonitorContext();
  const { preferences, saveFilterPreset } = usePagePreference(pageKey);
  const userStore = useUserStore();

  const userRoles = computed(() => userStore.userInfo?.roles ?? []);
  const canUseTableView = computed(() =>
    canUseTableViewByRoles(userRoles.value),
  );
  const savedFilters = computed(() => preferences.value.savedFilters ?? []);

  /** 保存当前筛选为预设 */
  function saveCurrentView(name: string): void {
    const filters = buildSavedViewFilters(monitorCtx.context.value);
    saveFilterPreset(name, filters);
  }

  /** 应用预设到当前上下文，返回是否成功 */
  function applyView(presetId: string): boolean {
    const preset = savedFilters.value.find((p) => p.id === presetId);
    if (!preset) return false;
    const patch = buildApplyPatch(preset, userRoles.value);
    monitorCtx.update(patch);
    return true;
  }

  return {
    savedFilters,
    canUseTableView,
    saveCurrentView,
    applyView,
  };
}
