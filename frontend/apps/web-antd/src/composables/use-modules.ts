/**
 * 模块热插拔 composable（IA 优化 P1）
 *
 * 登录后拉取 GET /system/modules，缓存已启用模块 key 集合，
 * 提供 moduleEnabled(key) 响应式判断。
 *
 * 设计要点：
 * - 模块级单例状态（reactive），组件和路由守卫均可使用
 * - 未加载时 moduleEnabled 返回 true（兼容首屏/加载中，避免闪烁）
 * - fetchModules 由路由守卫在登录后调用一次
 */
import { reactive, readonly } from 'vue';

import { getModulesApi } from '#/api/system';

export type ModuleKey =
  | 'assess'
  | 'config'
  | 'diagnosis'
  | 'handling'
  | 'monitor'
  | 'reports'
  | 'system'
  | 'tuning';

interface ModuleState {
  enabledKeys: Set<string>;
  loaded: boolean;
  loading: boolean;
}

const state = reactive<ModuleState>({
  enabledKeys: new Set<string>(),
  loaded: false,
  loading: false,
});

let pendingPromise: null | Promise<void> = null;

/**
 * 从后端拉取模块启用状态并刷新缓存。
 * 守卫在登录后、生成路由前调用；失败时回退全部启用（不阻塞登录）。
 */
export async function fetchModules(): Promise<void> {
  if (state.loading && pendingPromise) {
    return pendingPromise;
  }
  state.loading = true;
  pendingPromise = (async () => {
    try {
      const res = await getModulesApi();
      state.enabledKeys = new Set(res.enabledKeys);
      state.loaded = true;
    } catch {
      // 拉取失败时回退全部启用，避免白屏/阻塞
      state.enabledKeys = new Set([
        'assess',
        'config',
        'diagnosis',
        'handling',
        'monitor',
        'reports',
        'system',
        'tuning',
      ]);
      state.loaded = true;
    } finally {
      state.loading = false;
    }
  })();
  return pendingPromise;
}

/**
 * 判断模块是否启用（响应式）。
 * 未加载状态下返回 true，避免路由生成前闪烁；加载后按实际配置。
 */
export function moduleEnabled(key: string): boolean {
  if (!state.loaded) return true;
  return state.enabledKeys.has(key);
}

/** 返回已启用模块 key 的只读数组（响应式） */
export function getEnabledKeys(): string[] {
  return [...state.enabledKeys];
}

/**
 * 模块热插拔 composable
 *
 * @example
 * ```ts
 * const { moduleEnabled } = useModules();
 * // v-if="moduleEnabled('diagnosis')"
 * ```
 */
export function useModules() {
  return {
    moduleEnabled,
    fetchModules,
    getEnabledKeys,
    loaded: readonly(state as { loaded: boolean }),
  };
}
