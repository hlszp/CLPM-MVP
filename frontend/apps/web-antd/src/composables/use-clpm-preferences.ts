/**
 * CLPM 用户偏好持久化（P6-06 O06-NAV：服务端持久 + 浏览器缓存双制）
 *
 * 契约事实源：01a §3 + 03-个人收藏与工作接续数据设计：
 * - **PostgreSQL 为真相来源**：可跨设备的偏好（默认首页 default_home、
 *   筛选预设 filter_presets、排序预设 sort_presets 三个白名单 key）经
 *   GET/PUT /me/workspace-preferences 按单 key 读写，expectedRevision CAS
 *   + requestId 幂等；只在服务端确认后显示已保存，断网/失败显示未保存，
 *   不造离线成功、不建离线写队列；
 * - **浏览器仅缓存**：列配置/密度/默认时间窗/默认页大小等非白名单字段只
 *   进账户维度缓存；缓存 key = 部署地址 + 稳定用户 ID + 权限版本（角色与
 *   权限码散列），退出/换账号/撤权时清理；
 * - **旧匿名键 `clpm-preferences` 不自动导入**：无法证明归属；提供显式
 *   一次性导入（用户确认后逐 key PUT，服务端校验成功才生效，成功后移除
 *   旧键），未导入旧值不再被读取、不会覆盖服务端；
 * - 409 版本冲突不静默覆盖：刷新服务端状态，内容一致视为已保存，不一致
 *   进入 conflict 态由用户确认后重新保存。
 *
 * 服务端 filter_presets 载荷形态说明（05 冻结的通用安全校验：JSON 深度
 * ≤4、字符串不得含 ://）：逐页平铺 `{pageKey: [{id,name,page,createdAt,
 * filters}]}`，其中 filters 以 JSON 字符串携带（嵌套对象/数组会超深度
 * 限制）；读取时反序列化还原为 FilterPreset[]。
 */

import type { Ref } from 'vue';

import { reactive, ref, watch } from 'vue';

import { useAccessStore, useUserStore } from '@vben/stores';

import {
  extractRevisionConflict,
  getWorkspacePreferencesApi,
  newRequestId,
  putWorkspacePreferenceApi,
} from '#/api/personal-work';

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
  /** 保存的筛选预设（服务端 filter_presets 持久化，跨设备） */
  savedFilters?: FilterPreset[];
  /** 表格密度三档（A-07：compact/default/relaxed，默认 compact） */
  tableDensity?: string;
}

/** 偏好保存状态：saved=服务端已确认；saving=进行中；unsaved=失败/断网未保存；conflict=版本冲突已刷新待确认 */
export type PreferenceSaveState = 'conflict' | 'saved' | 'saving' | 'unsaved';

/* ── 常量 ── */

/** 旧匿名键（01a §3：不自动导入，仅显式一次性导入可识别非敏感项） */
const LEGACY_STORAGE_KEY = 'clpm-preferences';
const LEGACY_VERSION = '1.0';

/** 账户维度浏览器缓存（v2：部署地址+稳定用户 ID+权限版本隔离） */
const CACHE_PREFIX = 'clpm-prefs:';
const CACHE_VERSION = 'v2';
/** 记录最近登录稳定用户 ID（换账号检测用；非敏感） */
const LAST_USER_KEY = 'clpm-prefs:last-user';

/** 服务端白名单 key（schemas/services personal_workspace 冻结） */
export const SERVER_PREF_KEYS = {
  defaultHome: 'default_home',
  filterPresets: 'filter_presets',
  sortPresets: 'sort_presets',
} as const;

/* ── 模块级状态（全应用共享一份，usePagePreference 同 pageKey 共享同一 ref） ── */

interface ServerPrefEntry {
  payload: Record<string, any>;
  revision: number;
}

/** 服务端偏好（真相来源镜像；key → payload+revision） */
const serverItems = reactive<Record<string, ServerPrefEntry>>({});
/** 单 key 保存状态（未保存/冲突如实显示） */
const saveStates = reactive<Record<string, PreferenceSaveState>>({});
/** 保存失败后待重试的 payload（unsaved 态重试用；非离线队列，仅内存） */
const pendingPayloads = reactive<Record<string, Record<string, any>>>({});
/** 服务端首次同步是否完成 */
const serverLoaded = ref(false);
/** 服务端同步失败态（F-G1：失败如实登记、UI 显式提示，不阻断调用方流程） */
const serverSyncFailed = ref(false);
/** 当前已同步的稳定用户 ID（换账号后重新同步） */
let syncedUserId: null | string = null;
let syncPromise: null | Promise<void> = null;
/** 远端内容回灌本地时的守卫（避免触发 watch 回写） */
let applyingRemote = false;

/** 同 pageKey 共享的页面偏好 ref（跨实例天然一致） */
const pageStates = new Map<string, Ref<PagePreference>>();
/** 每页最近一次与服务端一致的 savedFilters 序列化（变更检测用） */
const lastSyncedFilters = new Map<string, string>();
/** 每页 watch 是否已注册 */
const watchedPages = new Set<string>();
/** 本会话写过的缓存 key（localStorage 不可枚举时的清理兜底） */
const sessionCacheKeys = new Set<string>();

/* ── 账户维度缓存 key（部署地址 + 稳定用户 ID + 权限版本） ── */

function safeAccount(): {
  codes: string[];
  roles: string[];
  userId: null | string;
} {
  try {
    const userStore = useUserStore();
    const accessStore = useAccessStore();
    return {
      codes: [...(accessStore.accessCodes ?? [])],
      roles: [...(userStore.userInfo?.roles ?? [])],
      userId: (userStore.userInfo?.userId as string | undefined) ?? null,
    };
  } catch {
    return { codes: [], roles: [], userId: null };
  }
}

/** 权限版本散列（djb2；撤权/换角色 → 缓存 key 变化 → 旧缓存不可见） */
function permVersionHash(parts: { codes: string[]; roles: string[] }): string {
  const raw = `${parts.roles.toSorted().join(',')}|${parts.codes.toSorted().join(',')}`;
  let h = 5381;
  for (let i = 0; i < raw.length; i++) {
    h = Math.trunc((h << 5) + h + (raw.codePointAt(i) ?? 0));
  }
  return (h >>> 0).toString(36);
}

function deploymentOrigin(): string {
  try {
    return globalThis.location?.origin || 'unknown';
  } catch {
    return 'unknown';
  }
}

/**
 * 当前缓存 key。未登录（无稳定用户 ID）时退到部署级 anon 槽位——
 * 仅登录前/无账号上下文使用，不会被导入任何账号（同旧匿名键纪律）。
 */
export function currentPreferenceCacheKey(): string {
  const account = safeAccount();
  const userPart = account.userId ?? 'anon';
  const permPart = account.userId ? permVersionHash(account) : 'anon';
  return `${CACHE_PREFIX}${CACHE_VERSION}:${deploymentOrigin()}:${userPart}:${permPart}`;
}

function storageAvailable(): boolean {
  try {
    return typeof localStorage !== 'undefined';
  } catch {
    return false;
  }
}

/** 读取账户维度缓存（浏览器缓存，非真相来源） */
function loadAccountCache(): Record<string, PagePreference> {
  if (!storageAvailable()) return {};
  try {
    const raw = localStorage.getItem(currentPreferenceCacheKey());
    if (!raw) return {};
    const data = JSON.parse(raw);
    if (data?.version !== CACHE_VERSION) return {};
    return data.preferences ?? {};
  } catch {
    return {};
  }
}

/** 写账户维度缓存（sync 立即写，不等服务端） */
function saveAccountCache(prefs: Record<string, PagePreference>) {
  if (!storageAvailable()) return;
  const key = currentPreferenceCacheKey();
  try {
    localStorage.setItem(
      key,
      JSON.stringify({
        preferences: prefs,
        updatedAt: new Date().toISOString(),
        version: CACHE_VERSION,
      }),
    );
    sessionCacheKeys.add(key);
  } catch {
    /* 缓存写失败不影响主流程（浏览器仅缓存） */
  }
}

/**
 * 清理本部署全部账户维度缓存（退出/换账号/撤权调用）。
 * localStorage 可枚举时按前缀清扫；不可枚举（部分测试 stub）时用会话内
 * 已知 key 兜底。
 */
export function clearAccountScopedCaches(): void {
  if (!storageAvailable()) return;
  const origin = deploymentOrigin();
  const prefix = `${CACHE_PREFIX}${CACHE_VERSION}:${origin}:`;
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k?.startsWith(prefix)) keys.push(k);
    }
    for (const k of keys) localStorage.removeItem(k);
  } catch {
    /* 环境不支持枚举时走会话兜底 */
  }
  for (const k of sessionCacheKeys) {
    try {
      localStorage.removeItem(k);
    } catch {
      /* noop */
    }
  }
  sessionCacheKeys.clear();
}

/* ── 服务端同步（GET /me/workspace-preferences） ── */

/** 服务端 filter_presets 载荷条目 → FilterPreset（filters 为 JSON 字符串） */
function deserializePreset(entry: any): FilterPreset | null {
  if (!entry || typeof entry !== 'object') return null;
  const { createdAt, id, name, page } = entry;
  if (typeof id !== 'string' || typeof name !== 'string') return null;
  let filters: Record<string, any> = {};
  if (typeof entry.filters === 'string' && entry.filters) {
    try {
      const parsed = JSON.parse(entry.filters);
      if (parsed && typeof parsed === 'object') filters = parsed;
    } catch {
      return null; // 无法解析的条目不展示（不编造筛选）
    }
  }
  return {
    createdAt: typeof createdAt === 'string' ? createdAt : '',
    filters,
    id,
    name,
    page: typeof page === 'string' ? page : '',
  };
}

/** FilterPreset → 服务端载荷条目（filters 序列化为 JSON 字符串） */
function serializePreset(p: FilterPreset): Record<string, any> {
  return {
    createdAt: p.createdAt,
    filters: JSON.stringify(p.filters ?? {}),
    id: p.id,
    name: p.name,
    page: p.page,
  };
}

function serializePresetsForPayload(presets: FilterPreset[]): string {
  return JSON.stringify(presets.map((p) => ({ ...p, filters: p.filters ?? {} })));
}

/** 从服务端载荷读某页筛选预设 */
function serverPresetsFor(pageKey: string): FilterPreset[] {
  const payload = serverItems[SERVER_PREF_KEYS.filterPresets]?.payload ?? {};
  const raw = payload[pageKey];
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => deserializePreset(entry))
    .filter((p): p is FilterPreset => p !== null);
}

/**
 * 服务端同步：拉取本人可写偏好并回灌各页 savedFilters。
 * **永不 reject**（F-G1）：同步失败（网络/404/5xx）如实登记
 * ``serverSyncFailed`` 并正常返回——偏好不可用不得阻断调用方页面挂载
 * 与业务数据加载；UI 据此显式提示"个人偏好暂不可用"，不伪造不静默。
 */
export async function syncPreferencesFromServer(): Promise<void> {
  const account = safeAccount();
  if (!account.userId) return; // 未登录无服务端偏好
  if (syncPromise) return syncPromise;
  syncPromise = (async () => {
    try {
      const data = await getWorkspacePreferencesApi();
      applyingRemote = true;
      try {
        for (const key of Object.keys(serverItems)) {
          Reflect.deleteProperty(serverItems, key);
        }
        for (const [key, item] of Object.entries(data.items ?? {})) {
          serverItems[key] = {
            payload: item.payload ?? {},
            revision: item.revision,
          };
        }
        syncedUserId = account.userId;
        // 回灌已实例化页面的 savedFilters（远端为真相）
        for (const [pageKey, state] of pageStates) {
          const presets = serverPresetsFor(pageKey);
          lastSyncedFilters.set(pageKey, serializePresetsForPayload(presets));
          state.value = { ...state.value, savedFilters: presets };
        }
        serverLoaded.value = true;
        serverSyncFailed.value = false;
      } finally {
        applyingRemote = false;
      }
    } catch {
      // 失败态如实登记（不伪造已同步）；模块状态保持同步前内容
      serverSyncFailed.value = true;
    } finally {
      syncPromise = null;
    }
  })();
  return syncPromise;
}

/* ── 服务端写入（单 key CAS + requestId 幂等；409 刷新不静默覆盖） ── */

/**
 * 按单 key 写服务端偏好。
 * - 成功 → saved；网络/服务失败 → unsaved（pendingPayloads 留存供显式重试）；
 * - 409 → 刷新远端：内容已一致视为 saved（幂等收敛），不一致进入 conflict
 *   （远端已被他处更新，已刷新展示，由用户确认后重新保存——不静默覆盖）。
 */
export async function putServerPreference(
  key: string,
  payload: Record<string, any>,
): Promise<boolean> {
  saveStates[key] = 'saving';
  try {
    const res = await putWorkspacePreferenceApi(key, {
      expectedRevision: serverItems[key]?.revision ?? 0,
      payload,
      requestId: newRequestId(),
    });
    serverItems[key] = { payload: res.payload, revision: res.revision };
    Reflect.deleteProperty(pendingPayloads, key);
    saveStates[key] = 'saved';
    return true;
  } catch (error) {
    const conflict = extractRevisionConflict(error);
    if (conflict) {
      try {
        await syncPreferencesFromServer();
      } catch {
        /* 刷新失败按冲突处理，保留本地待确认 */
      }
      const remote = serverItems[key]?.payload;
      if (remote && JSON.stringify(remote) === JSON.stringify(payload)) {
        Reflect.deleteProperty(pendingPayloads, key);
        saveStates[key] = 'saved';
        return true;
      }
      pendingPayloads[key] = payload;
      saveStates[key] = 'conflict';
      return false;
    }
    pendingPayloads[key] = payload;
    saveStates[key] = 'unsaved';
    return false;
  }
}

/** unsaved 态显式重试（用户触发；非自动离线队列） */
export async function retryPreferenceSave(key: string): Promise<boolean> {
  const payload = pendingPayloads[key];
  if (!payload) return false;
  return putServerPreference(key, payload);
}

/** 保存某页筛选预设到服务端 filter_presets（整 key 载荷 = 全部页面） */
async function persistFilterPresets(pageKey: string, presets: FilterPreset[]) {
  const base: Record<string, any> = {
    ...serverItems[SERVER_PREF_KEYS.filterPresets]?.payload,
    [pageKey]: presets.map((p) => serializePreset(p)),
  };
  const ok = await putServerPreference(SERVER_PREF_KEYS.filterPresets, base);
  if (ok) lastSyncedFilters.set(pageKey, serializePresetsForPayload(presets));
}

/* ── 默认首页（default_home 白名单 key；{path} 单字段） ── */

/** 默认首页路径形态校验（服务端校验同口径：站内路径；另排除无意义的 '/' 防根路径回环） */
export function isValidHomePath(path: unknown): path is string {
  return (
    typeof path === 'string' &&
    path.startsWith('/') &&
    !path.startsWith('//') &&
    path.length > 1 &&
    path.length <= 200
  );
}

/** 本人已保存的默认首页（服务端值；未设置/未同步/非法值为 null） */
export function getDefaultHomePreference(): null | string {
  const path = serverItems[SERVER_PREF_KEYS.defaultHome]?.payload?.path;
  return isValidHomePath(path) ? path : null;
}

/**
 * 登录/首页解析时读取本人 default_home 偏好（F-G2 消费链：直接 GET
 * /me/workspace-preferences，不经模块同步状态——登录时序上模块尚未同步）。
 * **永不 reject**：加载失败/无偏好/非法值 → null（调用方回退角色映射，
 * 不阻断登录）。
 */
export async function fetchDefaultHomePreference(): Promise<null | string> {
  try {
    const data = await getWorkspacePreferencesApi();
    const path = data.items?.[SERVER_PREF_KEYS.defaultHome]?.payload?.path;
    return isValidHomePath(path) ? path : null;
  } catch {
    return null;
  }
}

/** 设定默认首页（站内路径；服务端校验生效） */
export async function setDefaultHomePreference(
  path: string,
): Promise<boolean> {
  return putServerPreference(SERVER_PREF_KEYS.defaultHome, { path });
}

/* ── 旧匿名键显式一次性导入（不自动；用户确认；服务端校验生效） ── */

interface LegacyAnonymousPreferences {
  filterPresetCount: number;
  pages: string[];
}

function readLegacyAnonymous(): null | {
  preferences: Record<string, PagePreference>;
  summary: LegacyAnonymousPreferences;
} {
  if (!storageAvailable()) return null;
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data?.version !== LEGACY_VERSION || !data.preferences) return null;
    const preferences = data.preferences as Record<string, PagePreference>;
    const pages = Object.keys(preferences).filter(
      (k) => preferences[k] && Object.keys(preferences[k]).length > 0,
    );
    if (pages.length === 0) return null;
    const filterPresetCount = pages.reduce(
      (n, k) => n + (preferences[k]?.savedFilters?.length ?? 0),
      0,
    );
    return { preferences, summary: { filterPresetCount, pages } };
  } catch {
    return null;
  }
}

/** 检测旧匿名偏好（存在即返回摘要；页面据此提示用户选择导入/忽略） */
export function detectLegacyAnonymousPreferences(): LegacyAnonymousPreferences | null {
  return readLegacyAnonymous()?.summary ?? null;
}

/**
 * 显式一次性导入旧匿名偏好（用户确认后调用）。
 * 旧格式仅含 savedFilters 可识别非敏感项 → 合并进 filter_presets 逐 key
 * PUT（同 id 去重）；服务端校验成功后移除旧键，失败保留旧键供重试。
 */
export async function importLegacyAnonymousPreferences(): Promise<{
  failed: number;
  imported: number;
  saved: boolean;
  skipped: number;
}> {
  const legacy = readLegacyAnonymous();
  if (!legacy) return { failed: 0, imported: 0, saved: true, skipped: 0 };
  const base: Record<string, any> = {
    ...serverItems[SERVER_PREF_KEYS.filterPresets]?.payload,
  };
  let imported = 0;
  let skipped = 0;
  for (const [pageKey, pref] of Object.entries(legacy.preferences)) {
    const list = pref?.savedFilters ?? [];
    if (list.length === 0) continue;
    const existing: any[] = Array.isArray(base[pageKey]) ? base[pageKey] : [];
    const ids = new Set(existing.map((e: any) => e?.id));
    for (const p of list) {
      if (!p?.id || ids.has(p.id)) {
        skipped++;
        continue;
      }
      existing.push(serializePreset(p));
      ids.add(p.id);
      imported++;
    }
    if (existing.length > 0) base[pageKey] = existing;
  }
  if (imported > 0) {
    const ok = await putServerPreference(SERVER_PREF_KEYS.filterPresets, base);
    if (!ok) {
      return { failed: imported, imported: 0, saved: false, skipped };
    }
  }
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* noop */
  }
  // 导入内容回灌已实例化页面
  applyingRemote = true;
  try {
    for (const [pageKey, state] of pageStates) {
      const presets = serverPresetsFor(pageKey);
      lastSyncedFilters.set(pageKey, serializePresetsForPayload(presets));
      state.value = { ...state.value, savedFilters: presets };
    }
  } finally {
    applyingRemote = false;
  }
  return { failed: 0, imported, saved: true, skipped };
}

/** 用户选择忽略：移除旧键（新代码本就不读取，旧值不会再覆盖服务端） */
export function dismissLegacyAnonymousPreferences(): void {
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* noop */
  }
}

/* ── 账号生命周期钩子（auth store 调用） ── */

/**
 * 登录成功后登记稳定用户 ID：与上次登录不同（换账号）→ 清理本部署全部
 * 账户维度缓存并重置模块状态（上一个人的内容不能展示给下一个人）。
 */
export function noteClpmAccountLogin(userId: string): void {
  if (!storageAvailable()) return;
  let last: null | string = null;
  try {
    last = localStorage.getItem(LAST_USER_KEY);
    localStorage.setItem(LAST_USER_KEY, userId);
  } catch {
    /* noop */
  }
  if (last && last !== userId) {
    clearAccountScopedCaches();
    resetClpmPreferenceState();
  }
  // 同账号重复登录不清理（缓存仍是即时视图，服务端同步后对齐真相）
}

/** 退出登录：清理账户维度缓存 + 模块状态（服务端数据不受影响） */
export function handleClpmAccountLogout(): void {
  clearAccountScopedCaches();
  resetClpmPreferenceState();
  try {
    localStorage.removeItem(LAST_USER_KEY);
  } catch {
    /* noop */
  }
}

/** 重置模块级状态（不触碰 localStorage 缓存——缓存清理由上方函数负责） */
export function resetClpmPreferenceState(): void {
  for (const key of Object.keys(serverItems)) {
    Reflect.deleteProperty(serverItems, key);
  }
  for (const key of Object.keys(saveStates)) {
    Reflect.deleteProperty(saveStates, key);
  }
  for (const key of Object.keys(pendingPayloads)) {
    Reflect.deleteProperty(pendingPayloads, key);
  }
  serverLoaded.value = false;
  serverSyncFailed.value = false;
  syncedUserId = null;
  pageStates.clear();
  lastSyncedFilters.clear();
  watchedPages.clear();
}

/** 服务端同步状态（页面"已保存/未保存/偏好不可用"徽标用） */
export function useWorkspacePreferenceState() {
  return {
    saveStates,
    serverLoaded,
    serverSyncFailed,
    syncedUserId: () => syncedUserId,
  };
}

/* ── 页面偏好（兼容既有消费方签名） ── */

/**
 * 获取页面偏好
 *
 * @param pageKey 页面标识，如 'loop-monitor'、'metric-dashboard'
 *
 * 行为（P6-06 双制）：
 * - 列配置/密度/时间窗/页大小 → 账户维度浏览器缓存（即时，随账号隔离）；
 * - savedFilters → 服务端 filter_presets（同步完成后以服务端为准回灌）；
 *   变更经 watch 自动 PUT（CAS+幂等），失败/断网 saveStates=unsaved 如实
 *   显示，不在本地冒充已保存。
 */
export function usePagePreference(pageKey: string) {
  let state = pageStates.get(pageKey);
  if (!state) {
    const initial: PagePreference = {
      ...loadAccountCache()[pageKey],
      // 服务端已同步时以服务端筛选预设为准（真相来源）
      ...(syncedUserId ? { savedFilters: serverPresetsFor(pageKey) } : {}),
    };
    state = ref<PagePreference>(initial) as Ref<PagePreference>;
    pageStates.set(pageKey, state);
    lastSyncedFilters.set(
      pageKey,
      serializePresetsForPayload(state.value.savedFilters ?? []),
    );
  }

  // 自动保存（缓存即时写 + savedFilters 变更触发服务端 PUT）
  if (!watchedPages.has(pageKey)) {
    watchedPages.add(pageKey);
    watch(
      state,
      (val) => {
        if (applyingRemote) return;
        // 账户维度缓存（浏览器仅缓存；非真相来源）
        const all = loadAccountCache();
        all[pageKey] = val;
        saveAccountCache(all);
        // 筛选预设 → 服务端（有账号时；无账号仅缓存，不冒充跨设备保存）
        const account = safeAccount();
        if (!account.userId) return;
        const presets = val.savedFilters ?? [];
        const serialized = serializePresetsForPayload(presets);
        if (serialized === lastSyncedFilters.get(pageKey)) return;
        // 先登记为本地待同步，PUT 成功后校准（失败由 saveStates 如实显示）
        lastSyncedFilters.set(pageKey, serialized);
        void persistFilterPresets(pageKey, presets);
      },
      { deep: true },
    );
  }

  // 首次使用触发服务端同步（登录后；同步完成回灌 savedFilters）
  const account = safeAccount();
  if (account.userId && syncedUserId !== account.userId && !syncPromise) {
    void syncPreferencesFromServer();
  }

  /** 更新表格列配置 */
  function updateColumns(columns: ColumnConfig[]) {
    state!.value.columns = columns;
  }

  /** 设置默认时间窗 */
  function setDefaultTimeWindow(tw: string) {
    state!.value.defaultTimeWindow = tw;
  }

  /** 设置默认每页条数 */
  function setDefaultPageSize(size: number) {
    state!.value.defaultPageSize = size;
  }

  /** 保存筛选预设（服务端持久化；同一 pageKey 跨设备可见） */
  function saveFilterPreset(name: string, filters: Record<string, any>) {
    if (!state!.value.savedFilters) {
      state!.value.savedFilters = [];
    }
    state!.value.savedFilters.push({
      createdAt: new Date().toISOString(),
      filters,
      id: `${Date.now()}`,
      name,
      page: pageKey,
    });
  }

  /** 删除筛选预设 */
  function deleteFilterPreset(id: string) {
    if (!state!.value.savedFilters) return;
    state!.value.savedFilters = state!.value.savedFilters.filter(
      (f) => f.id !== id,
    );
  }

  /** 重置为默认（仅当前页；savedFilters 重置同步走服务端 PUT） */
  function reset() {
    state!.value = {};
  }

  return {
    preferences: state,
    updateColumns,
    setDefaultTimeWindow,
    setDefaultPageSize,
    saveFilterPreset,
    deleteFilterPreset,
    reset,
  };
}
