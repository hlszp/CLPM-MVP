/**
 * CLPM 用户偏好持久化
 *
 * 提供表格列配置、默认时间窗、筛选条件预设等偏好的 localStorage 持久化能力。
 * - 按 pageKey 维度隔离不同页面的偏好
 * - 自动保存：偏好变更后自动同步到 localStorage
 * - 版本号 VERSION 用于后续数据迁移（版本不兼容时自动重置）
 * - 重置功能只重置当前页面偏好，不影响其他页面
 *
 * 后端同步接口预留：当前仅 localStorage，后续可通过替换 loadPreferences /
 * savePreferences 两个内部函数接入后端接口。
 */
import { ref, watch } from 'vue';

/** 表格列配置 */
export interface ColumnConfig {
  /** 列 dataIndex 或 key */
  key: string;
  /** 列标题 */
  label: string;
  /** 是否显示 */
  visible: boolean;
  /** 列宽 */
  width?: number;
  /** 排序顺序 */
  order?: number;
}

/** 筛选条件预设 */
export interface FilterPreset {
  /** 预设 ID */
  id: string;
  /** 预设名称 */
  name: string;
  /** 页面标识 */
  page: string;
  /** 筛选条件 */
  filters: Record<string, any>;
  /** 创建时间 */
  createdAt: string;
}

/** 页面偏好 */
export interface PagePreference {
  /** 表格列配置 */
  columns?: ColumnConfig[];
  /** 默认时间窗 */
  defaultTimeWindow?: string;
  /** 默认每页条数 */
  defaultPageSize?: number;
  /** 保存的筛选预设 */
  savedFilters?: FilterPreset[];
}

const STORAGE_KEY = 'clpm-preferences';
const VERSION = '1.0';

/** 读取所有偏好 */
function loadPreferences(): Record<string, PagePreference> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw);
    if (data.version !== VERSION) return {}; // 版本不兼容则重置
    return data.preferences || {};
  } catch {
    return {};
  }
}

/** 保存所有偏好 */
function savePreferences(prefs: Record<string, PagePreference>) {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      version: VERSION,
      preferences: prefs,
      updatedAt: new Date().toISOString(),
    }),
  );
}

/**
 * 获取页面偏好
 *
 * @param pageKey 页面标识，如 'loop-monitor'、'metric-dashboard'
 */
export function usePagePreference(pageKey: string) {
  const preferences = ref<PagePreference>(loadPreferences()[pageKey] || {});

  // 自动保存
  watch(
    preferences,
    (val) => {
      const all = loadPreferences();
      all[pageKey] = val;
      savePreferences(all);
    },
    { deep: true },
  );

  /** 更新表格列配置 */
  function updateColumns(columns: ColumnConfig[]) {
    preferences.value.columns = columns;
  }

  /** 设置默认时间窗 */
  function setDefaultTimeWindow(tw: string) {
    preferences.value.defaultTimeWindow = tw;
  }

  /** 设置默认每页条数 */
  function setDefaultPageSize(size: number) {
    preferences.value.defaultPageSize = size;
  }

  /** 保存筛选预设 */
  function saveFilterPreset(name: string, filters: Record<string, any>) {
    if (!preferences.value.savedFilters) {
      preferences.value.savedFilters = [];
    }
    preferences.value.savedFilters.push({
      id: `${Date.now()}`,
      name,
      page: pageKey,
      filters,
      createdAt: new Date().toISOString(),
    });
  }

  /** 删除筛选预设 */
  function deleteFilterPreset(id: string) {
    if (!preferences.value.savedFilters) return;
    preferences.value.savedFilters = preferences.value.savedFilters.filter(
      (f) => f.id !== id,
    );
  }

  /** 重置为默认 */
  function reset() {
    preferences.value = {};
  }

  return {
    preferences,
    updateColumns,
    setDefaultTimeWindow,
    setDefaultPageSize,
    saveFilterPreset,
    deleteFilterPreset,
    reset,
  };
}
