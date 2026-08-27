/**
 * 工作台 v2.0 全局 Pinia store（方案 §5.1 F-GL-04）
 *
 * 持有跨 Tab 共享状态：范围(scope)+ 时间窗口(window)+ 自定义起止 +
 * 最近刷新时间 + 模块 4 态 plugins + 铃铛未读 unread。
 * 所有 Tab 通过本 store 联动：范围/窗口切换 → 5 Tab 自动刷新（M2 接入）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, ref } from 'vue';

import { defineStore } from 'pinia';

import {
  getWorkbenchPluginsApi,
  getWorkbenchScopeTreeApi,
  type WorkbenchApi,
} from '#/api/workbench';

export const useWorkbenchStore = defineStore('workbench', () => {
  // ============ 范围与时间窗口（5 Tab 共享，全局联动） ============
  const scopeType = ref<WorkbenchApi.ScopeType>('GLOBAL');
  const scopeId = ref<null | number>(null);
  const timeWindow = ref<WorkbenchApi.TimeWindow>('24h');
  /** 范围选择器节点列表（工厂 + 装置，A-00 scope-tree） */
  const scopeTree = ref<WorkbenchApi.ScopeNode[]>([]);
  const customStart = ref<null | string>(null);
  const customEnd = ref<null | string>(null);
  const lastRefreshAt = ref<null | number>(null);

  // ============ 模块 4 态 + 铃铛未读 ============
  /** A-10 模块 4 态列表（真实数据源，4 态 dot/pill 渲染依据） */
  const plugins = ref<WorkbenchApi.Plugin[]>([]);
  /** A-E5 铃铛未读计数（M1 桩为 0；M2 接 WS 推送 < 200ms） */
  const unreadCount = ref(0);
  /** 当前激活 Tab（统一框架 v-show 切换；跨 Tab 跳转如评估→诊断用 setActiveTab） */
  const activeTab = ref('overview');
  const loading = ref(false);
  // ============ 处置漏斗联动（F-OV-05） ============
  /** 总览漏斗点击 → 切处置 Tab + 高亮对应泳道；切 tab / 选任务卡时清空 */
  const handlingLaneFilter = ref<HandlingApi.OrderStatus | null>(null);

  // ============ 计算属性 ============
  /** 已启用模块 key 集合（CORE + ENABLED；用于 Tab 可点击判断） */
  const enabledModuleKeys = computed(() =>
    plugins.value
      .filter((p) => p.status === 'CORE' || p.status === 'ENABLED')
      .map((p) => p.module_key),
  );

  /** 统一请求参数（5 Tab 数据加载复用；customStart/customEnd 仅自定义窗口下发） */
  const scopeParams = computed<WorkbenchApi.ScopeParams>(() => ({
    scopeId: scopeId.value ?? undefined,
    scopeType: scopeType.value,
    window: timeWindow.value,
    ...(customStart.value ? { customStart: customStart.value } : {}),
    ...(customEnd.value ? { customEnd: customEnd.value } : {}),
  }));

  // ============ Actions ============
  function setScope(type: WorkbenchApi.ScopeType, id: null | number) {
    scopeType.value = type;
    scopeId.value = id;
  }

  /** 切换滚动时间窗口（24h/7d/30d）；切换时清空自定义起止 */
  function setWindow(win: WorkbenchApi.TimeWindow) {
    timeWindow.value = win;
    customStart.value = null;
    customEnd.value = null;
  }

  /** 自定义时间窗口（ISO8601 起止） */
  function setCustomRange(start: string, end: string) {
    customStart.value = start;
    customEnd.value = end;
  }

  /** 标记本次刷新时刻（StatusBar 展示刷新时间） */
  function markRefreshed() {
    lastRefreshAt.value = Date.now();
  }

  /** 取模块当前 4 态（null = 未注册/未知） */
  function getModuleStatus(key: string): null | WorkbenchApi.ModuleStatus {
    return plugins.value.find((p) => p.module_key === key)?.status ?? null;
  }

  /** A-10 加载模块 4 态列表（M1 真实调用；4 态 dot/pill 数据源） */
  async function loadPlugins() {
    loading.value = true;
    try {
      const res = await getWorkbenchPluginsApi();
      plugins.value = res?.plugins ?? [];
    } catch {
      plugins.value = [];
    } finally {
      loading.value = false;
    }
  }

  /** A-E5 加载未读计数（M1 桩：后端端点待 M2，保持 0） */
  async function loadUnread() {
    // TODO: M2 接 WS 未读计数端点 + WS 推送实时更新
    unreadCount.value = 0;
  }

  /** A-00 加载范围选择器节点列表（工厂 + 装置） */
  async function loadScopeTree() {
    try {
      const res = await getWorkbenchScopeTreeApi();
      scopeTree.value = res ?? [];
    } catch {
      scopeTree.value = [];
    }
  }

  /** 切换激活 Tab（统一框架 v-show 切换，非路由跳转）；切离处置 Tab 时清空泳道过滤 */
  function setActiveTab(key: string) {
    activeTab.value = key;
    if (key !== 'handling') handlingLaneFilter.value = null;
  }

  /** F-OV-05 漏斗联动：设置处置泳道过滤（null=清除） */
  function setHandlingLaneFilter(lane: HandlingApi.OrderStatus | null) {
    handlingLaneFilter.value = lane;
  }

  /** A-12 批量标记已读（M2 铃铛抽屉"全部已读"按钮接入） */
  async function markAllRead(eventIds: number[]) {
    // TODO: M2 调 markWorkbenchEventsReadApi({ event_ids: eventIds })
    if (eventIds.length === 0) return;
    unreadCount.value = 0;
  }

  return {
    // 状态
    activeTab,
    customEnd,
    customStart,
    handlingLaneFilter,
    lastRefreshAt,
    loading,
    plugins,
    scopeId,
    scopeParams,
    scopeTree,
    scopeType,
    timeWindow,
    unreadCount,
    // 计算
    enabledModuleKeys,
    // actions
    getModuleStatus,
    loadPlugins,
    loadScopeTree,
    loadUnread,
    markAllRead,
    markRefreshed,
    setCustomRange,
    setActiveTab,
    setHandlingLaneFilter,
    setScope,
    setWindow,
  };
});
