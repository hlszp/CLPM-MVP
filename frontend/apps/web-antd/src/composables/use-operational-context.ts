/**
 * use-operational-context.ts
 * Phase 1 共享载体：全模块统一操作上下文
 *
 * 职责：
 * 1. 从 URL 解析导航上下文（loopId, from, section, anchor, eventId, trackerId, taskId, timeWindow）
 * 2. 调用 getWorkbenchSummaryApi 加载回路工作台摘要（MonitorApi.WorkbenchSummary）
 * 3. 提供 navigateWith/updateUrl 等跨模块导航 API
 * 4. 统一 loading/empty/partial/stale/error/ready 六态
 */

import { computed, inject, provide, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { getWorkbenchSummaryApi } from '#/api/monitor';
import type { MonitorApi } from '#/api/monitor';
import type { DeepLink, StateFace, UrlContext } from './types/operational-context';
import { URL_CONTEXT_KEYS } from './types/operational-context';

function createDefaultUrlContext(): UrlContext {
  return {
    loopId: null,
    from: null,
    section: null,
    anchor: null,
    eventId: null,
    trackerId: null,
    taskId: null,
    timeWindow: '24h',
    plantNodeId: null,
  };
}

function parseUrlContext(query: Record<string, unknown>): UrlContext {
  const ctx = createDefaultUrlContext();
  const getStr = (k: string): string | null => {
    const v = query[k];
    return typeof v === 'string' && v ? v : null;
  };
  ctx.loopId = getStr(URL_CONTEXT_KEYS.loopId);
  const from = getStr(URL_CONTEXT_KEYS.from);
  if (from === 'overview' || from === 'list' || from === 'attention') ctx.from = from;
  ctx.section = getStr(URL_CONTEXT_KEYS.section);
  ctx.anchor = getStr(URL_CONTEXT_KEYS.anchor);
  ctx.eventId = getStr(URL_CONTEXT_KEYS.eventId);
  ctx.trackerId = getStr(URL_CONTEXT_KEYS.trackerId);
  ctx.taskId = getStr(URL_CONTEXT_KEYS.taskId);
  const tw = getStr(URL_CONTEXT_KEYS.timeWindow);
  if (tw === '24h' || tw === '7d' || tw === '30d') ctx.timeWindow = tw;
  ctx.plantNodeId = getStr(URL_CONTEXT_KEYS.plantNodeId);
  return ctx;
}

/**
 * useOperationalContext
 * 全模块统一操作上下文 composable
 */
export function useOperationalContext() {
  const route = useRoute();
  const router = useRouter();

  const summary = ref<MonitorApi.WorkbenchSummary | null>(null);
  const urlContext = ref<UrlContext>(createDefaultUrlContext());
  const loading = ref(false);
  const error = ref<Error | null>(null);

  function parseFromRoute(): UrlContext {
    return parseUrlContext(route.query as Record<string, unknown>);
  }

  async function loadByLoopId(loopId: string) {
    loading.value = true;
    error.value = null;
    try {
      summary.value = await getWorkbenchSummaryApi(loopId);
    } catch (e) {
      error.value = e instanceof Error ? e : new Error(String(e));
      summary.value = null;
    } finally {
      loading.value = false;
    }
  }

  async function loadFromRoute() {
    urlContext.value = parseFromRoute();
    if (urlContext.value.loopId) {
      await loadByLoopId(urlContext.value.loopId);
    } else {
      summary.value = null;
    }
  }

  function updateUrl(patch: Partial<UrlContext>) {
    const newQuery: Record<string, string> = { ...(route.query as Record<string, string>) };
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === undefined) {
        delete newQuery[k];
      } else {
        newQuery[k] = String(v);
      }
    }
    router.replace({ query: newQuery });
    urlContext.value = { ...urlContext.value, ...patch };
  }

  function navigateWith(target: {
    path: string;
    loopId?: string | null;
    from?: UrlContext['from'];
    section?: string | null;
    anchor?: string | null;
    eventId?: string | null;
    trackerId?: string | null;
    taskId?: string | null;
    timeWindow?: UrlContext['timeWindow'];
    plantNodeId?: string | null;
    replace?: boolean;
  }) {
    const query: Record<string, string> = {};
    const loopIdToUse = target.loopId ?? urlContext.value.loopId;
    if (loopIdToUse) query[URL_CONTEXT_KEYS.loopId] = loopIdToUse;
    if (target.from) query[URL_CONTEXT_KEYS.from] = target.from;
    else if (urlContext.value.from) query[URL_CONTEXT_KEYS.from] = urlContext.value.from;
    if (target.section) query[URL_CONTEXT_KEYS.section] = target.section;
    if (target.anchor) query[URL_CONTEXT_KEYS.anchor] = target.anchor;
    if (target.eventId) query[URL_CONTEXT_KEYS.eventId] = target.eventId;
    if (target.trackerId) query[URL_CONTEXT_KEYS.trackerId] = target.trackerId;
    if (target.taskId) query[URL_CONTEXT_KEYS.taskId] = target.taskId;
    if (target.timeWindow) query[URL_CONTEXT_KEYS.timeWindow] = target.timeWindow;
    else query[URL_CONTEXT_KEYS.timeWindow] = urlContext.value.timeWindow;
    if (target.plantNodeId) query[URL_CONTEXT_KEYS.plantNodeId] = target.plantNodeId;

    const nav = target.replace ? router.replace : router.push;
    nav({ path: target.path, query });
  }

  // 监听路由变化自动重载
  watch(
    () => route.fullPath,
    () => {
      loadFromRoute();
    },
    { immediate: true },
  );

  // ----- 六态计算 -----
  const stateFace = computed<StateFace>(() => {
    if (loading.value) return 'loading';
    if (error.value) return 'error';
    if (!summary.value || !urlContext.value.loopId) return 'empty';
    if (summary.value.partial) return 'partial';
    if (summary.value.dataFreshness.status === 'DELAYED') return 'stale';
    return 'ready';
  });

  // ----- 派生导航 -----
  const navigation = computed(() => ({
    from: urlContext.value.from,
    backTo: urlContext.value.from === 'overview'
      ? { path: '/dashboard/workbench' } as DeepLink
      : null,
  }));

  // ----- 便捷访问器（直接访问 summary 字段） -----
  const scope = computed(() => summary.value);
  const nextAction = computed(() => summary.value?.nextAction ?? null);
  const assessment = computed(() => summary.value?.assessment ?? null);
  const diagnosis = computed(() => summary.value?.diagnosis ?? null);
  const tuning = computed(() => summary.value?.tuning ?? null);
  const trackerTimeline = computed(() => summary.value?.trackerTimeline ?? null);
  const dataHealth = computed(() => summary.value?.dataHealth ?? null);
  const scoreTrend = computed(() => summary.value?.scoreTrend ?? null);
  const activeAttention = computed(() => summary.value?.activeAttention ?? null);
  const lifecycle = computed(() => summary.value?.lifecycle ?? null);
  const hasTracker = computed(() => summary.value?.trackerTimeline?.trackerId != null);
  const unavailableSections = computed(() => summary.value?.unavailableSections ?? []);

  function isSectionAvailable(section: string): boolean {
    return !unavailableSections.value.includes(section);
  }

  // 执行 nextAction（如果有 target 路由）
  function executeNextAction() {
    const action = nextAction.value;
    if (!action?.enabled || !action.target) return;
    const { route: targetRoute, query: targetQuery } = action.target;
    const mergedQuery = { ...(route.query as Record<string, string>), ...targetQuery };
    router.push({ path: targetRoute, query: mergedQuery });
  }

  return {
    // 原始状态
    summary,
    urlContext,
    loading,
    error,
    // 核心方法
    loadFromRoute,
    loadByLoopId,
    updateUrl,
    navigateWith,
    parseFromRoute,
    executeNextAction,
    // 派生状态
    stateFace,
    navigation,
    // 便捷访问器
    scope,
    nextAction,
    assessment,
    diagnosis,
    tuning,
    trackerTimeline,
    dataHealth,
    scoreTrend,
    activeAttention,
    lifecycle,
    hasTracker,
    unavailableSections,
    isSectionAvailable,
  };
}

// ---------------------------------------------------------------------------
// Provider / Inject 跨组件共享
// ---------------------------------------------------------------------------

const OPERATIONAL_CONTEXT_KEY = Symbol('operational-context');

export type OperationalContextInstance = ReturnType<typeof useOperationalContext>;

export function provideOperationalContext(instance?: OperationalContextInstance): OperationalContextInstance {
  const ctx = instance ?? useOperationalContext();
  provide(OPERATIONAL_CONTEXT_KEY, ctx);
  return ctx;
}

export function injectOperationalContext(): OperationalContextInstance | null {
  return inject<OperationalContextInstance | null>(OPERATIONAL_CONTEXT_KEY, null);
}

export function injectOperationalContextOrThrow(): OperationalContextInstance {
  const ctx = inject<OperationalContextInstance | null>(OPERATIONAL_CONTEXT_KEY, null);
  if (!ctx) {
    throw new Error('injectOperationalContextOrThrow: no OperationalContext provider found');
  }
  return ctx;
}
