/**
 * 驾驶舱全局 Pinia store（方案 11 §5.3 / §9）
 *
 * 持有跨页（总览 /cockpit 与回路 /cockpit/loops）共享状态：
 * - timeWindow：时间窗总开关（24h/7d/30d，默认 24h），切换 Tab 页保持
 * - autoRefreshPaused：自动刷新暂停开关（C5 混合刷新预留）
 * - theme：驾驶舱局部主题（dark/light，localStorage 键 cockpit-theme 持久化，
 *   仅作用于 .cockpit-root 容器，不影响后台 vben 全局主题）
 */
import type { CockpitApi } from '#/api/cockpit';

import { ref } from 'vue';

import { defineStore } from 'pinia';

export type CockpitTheme = 'dark' | 'light';

const THEME_STORAGE_KEY = 'cockpit-theme';

function readStoredTheme(): CockpitTheme {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === 'light'
      ? 'light'
      : 'dark';
  } catch {
    return 'dark';
  }
}

/** 页2 模式筛选键（五档控制模式；对应后端 MODE 标准值 0~4） */
export type CockpitModeKey = 'APC' | 'AUTO' | 'CAS' | 'MANUAL' | 'REMOTE';

/** 页2 排序方式（评分降序 / 劣化降序） */
export type CockpitLoopsSortBy = 'degradeDesc' | 'scoreDesc';

/** 页2 卡片墙筛选排序状态（方案 11 §6.3） */
export interface CockpitLoopsFilters {
  /** 等级多选（空=全部五档） */
  grades: CockpitApi.GradeKey[];
  /** 模式多选（空=全部） */
  modes: CockpitModeKey[];
  sortBy: CockpitLoopsSortBy;
}

function defaultLoopsFilters(): CockpitLoopsFilters {
  return { grades: [], modes: [], sortBy: 'scoreDesc' };
}

export const useCockpitStore = defineStore('cockpit', () => {
  /** 时间窗总开关（近 24h / 近 7 天 / 近 30 天，默认 24h） */
  const timeWindow = ref<CockpitApi.TimeWindow>('24h');
  /** 自动刷新暂停开关（挂屏场景暂停 5min/60s 轮询） */
  const autoRefreshPaused = ref(false);
  /** 手动刷新节拍（顶栏刷新按钮 ++，各页面 watch 触发全量重拉） */
  const refreshTick = ref(0);
  /** 驾驶舱局部主题（默认深色，适合投屏） */
  const theme = ref<CockpitTheme>(readStoredTheme());

  // ------------------------------------------------------------------
  // 页2 回路状态墙（C4，方案 11 §6）
  // ------------------------------------------------------------------
  /** 选中装置树节点（plant_node.id；null=未选择，页面解析为全厂根节点） */
  const loopsNodeId = ref<null | string>(null);
  /** 选中回路（驱动右侧详情面板态二；null=聚合视图） */
  const loopsSelectedLoopId = ref<null | string>(null);
  /** 卡片墙页码（1 起，每页 20） */
  const loopsPage = ref(1);
  /** 等级/模式筛选 + 排序 */
  const loopsFilters = ref<CockpitLoopsFilters>(defaultLoopsFilters());

  function setLoopsNode(nodeId: null | string) {
    loopsNodeId.value = nodeId;
  }

  function selectLoop(loopId: null | string) {
    loopsSelectedLoopId.value = loopId;
  }

  function setLoopsPage(page: number) {
    loopsPage.value = page;
  }

  function setLoopsFilters(patch: Partial<CockpitLoopsFilters>) {
    loopsFilters.value = { ...loopsFilters.value, ...patch };
  }

  function setTimeWindow(win: CockpitApi.TimeWindow) {
    timeWindow.value = win;
  }

  function setAutoRefreshPaused(paused: boolean) {
    autoRefreshPaused.value = paused;
  }

  /** 手动刷新：refreshTick ++，驱动当前页全部区块重拉 */
  function triggerRefresh() {
    refreshTick.value += 1;
  }

  function setTheme(value: CockpitTheme) {
    theme.value = value;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, value);
    } catch {
      /* localStorage 不可用时仅保持会话内生效 */
    }
  }

  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark');
  }

  function $reset() {
    timeWindow.value = '24h';
    autoRefreshPaused.value = false;
    refreshTick.value = 0;
    loopsNodeId.value = null;
    loopsSelectedLoopId.value = null;
    loopsPage.value = 1;
    loopsFilters.value = defaultLoopsFilters();
    // theme 为用户显式偏好，登出不重置
  }

  return {
    $reset,
    autoRefreshPaused,
    loopsFilters,
    refreshTick,
    loopsNodeId,
    loopsPage,
    loopsSelectedLoopId,
    selectLoop,
    setAutoRefreshPaused,
    setLoopsFilters,
    setLoopsNode,
    setLoopsPage,
    setTheme,
    setTimeWindow,
    theme,
    timeWindow,
    toggleTheme,
    triggerRefresh,
  };
});
