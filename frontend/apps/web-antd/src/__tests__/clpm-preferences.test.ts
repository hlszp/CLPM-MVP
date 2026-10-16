/**
 * P6-06（O06-NAV）use-clpm-preferences 双制持久化测试
 *
 * 覆盖任务书验证口径：
 * - 断网未保存态：PUT 网络失败 → saveStates=unsaved 如实显示，不造离线
 *   成功；显式重试成功后才 saved；
 * - 缓存隔离：缓存 key 含部署地址+稳定用户 ID+权限版本——双 userId 隔离、
 *   撤权（权限码变化）隔离；换账号/退出清理；
 * - 409 不静默覆盖：版本冲突刷新后远端内容不一致 → conflict 态保留本地
 *   待确认，不用旧载荷覆盖远端；
 * - 匿名 clpm-preferences 不自动导入：显式一次性导入经服务端 PUT 生效，
 *   成功后移除旧键；忽略则旧值不再被读取。
 */
import { useAccessStore, useUserStore } from '@vben/stores';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getWorkspacePreferencesApi, putWorkspacePreferenceApi } = vi.hoisted(
  () => ({
    getWorkspacePreferencesApi: vi.fn(),
    putWorkspacePreferenceApi: vi.fn(),
  }),
);

vi.mock('#/api/personal-work', async () => {
  const actual = await vi.importActual<any>('#/api/personal-work');
  return {
    ...actual,
    getWorkspacePreferencesApi: (...a: any[]) => getWorkspacePreferencesApi(...a),
    putWorkspacePreferenceApi: (...a: any[]) => putWorkspacePreferenceApi(...a),
  };
});

import { nextTick } from 'vue';

import {
  currentPreferenceCacheKey,
  detectLegacyAnonymousPreferences,
  dismissLegacyAnonymousPreferences,
  fetchDefaultHomePreference,
  getDefaultHomePreference,
  handleClpmAccountLogout,
  importLegacyAnonymousPreferences,
  isValidHomePath,
  noteClpmAccountLogin,
  resetClpmPreferenceState,
  retryPreferenceSave,
  setDefaultHomePreference,
  syncPreferencesFromServer,
  usePagePreference,
  useWorkspacePreferenceState,
} from '#/composables/use-clpm-preferences';

/** 完整 Storage 语义 stub（测试环境 localStorage 无 clear/removeItem/枚举） */
function installLocalStorageStub() {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => store.get(k) ?? null,
    key: (i: number) => [...store.keys()][i] ?? null,
    removeItem: (k: string) => {
      store.delete(k);
    },
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
  });
  return store;
}

function setAccount(
  userId: null | string,
  roles: string[] = ['IC_ENGINEER'],
  codes: string[] = ['read:a'],
) {
  const userStore = useUserStore();
  const accessStore = useAccessStore();
  userStore.setUserInfo(
    userId ? ({ realName: 'T', roles, userId, username: 't' } as any) : null,
  );
  accessStore.setAccessCodes(codes);
}

function conflict409(data: Record<string, any>) {
  return {
    response: {
      data: {
        code: 'ERR_REVISION_CONFLICT',
        data,
        message: '偏好版本冲突，请刷新后重试',
      },
      status: 409,
    },
  };
}

describe('use-clpm-preferences（P6-06 双制持久化）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    installLocalStorageStub();
    resetClpmPreferenceState();
    getWorkspacePreferencesApi.mockReset().mockResolvedValue({
      items: {},
      readAt: '2026-10-14T01:00:00Z',
    });
    putWorkspacePreferenceApi.mockReset();
  });

  describe('断网未保存态（不造离线成功）', () => {
    it('PUT 网络失败 → unsaved；显式重试成功 → saved 且可读回', async () => {
      setAccount('user-a');
      putWorkspacePreferenceApi.mockRejectedValueOnce(
        new TypeError('Network Error'),
      );
      const ok = await setDefaultHomePreference('/my-work');
      expect(ok).toBe(false);
      const { saveStates } = useWorkspacePreferenceState();
      expect(saveStates.default_home).toBe('unsaved');
      // 未保存成功不得读回值（不冒充已保存）
      expect(getDefaultHomePreference()).toBeNull();

      putWorkspacePreferenceApi.mockResolvedValueOnce({
        payload: { path: '/my-work' },
        preferenceKey: 'default_home',
        replayed: false,
        revision: 1,
        schemaVersion: 1,
        updatedAt: '2026-10-14T01:01:00Z',
      });
      const retryOk = await retryPreferenceSave('default_home');
      expect(retryOk).toBe(true);
      expect(saveStates.default_home).toBe('saved');
      expect(getDefaultHomePreference()).toBe('/my-work');
    });

    it('409 冲突：远端内容不一致 → conflict 态（不静默覆盖）；一致 → 幂等 saved', async () => {
      setAccount('user-a');
      // 远端已有他处写入的不同值（revision 3）
      getWorkspacePreferencesApi.mockResolvedValueOnce({
        items: {
          default_home: {
            payload: { path: '/cockpit' },
            revision: 3,
            schemaVersion: 1,
            updatedAt: '2026-10-14T01:02:00Z',
          },
        },
        readAt: '2026-10-14T01:02:00Z',
      });
      putWorkspacePreferenceApi.mockRejectedValueOnce(
        conflict409({ currentRevision: 3, preferenceKey: 'default_home' }),
      );
      const ok = await setDefaultHomePreference('/my-work');
      expect(ok).toBe(false);
      const { saveStates } = useWorkspacePreferenceState();
      expect(saveStates.default_home).toBe('conflict');
      // 已刷新远端值显示（不用本地待确认值覆盖）
      expect(getDefaultHomePreference()).toBe('/cockpit');

      // 远端内容已与本地一致（幂等收敛）→ saved
      getWorkspacePreferencesApi.mockResolvedValueOnce({
        items: {
          default_home: {
            payload: { path: '/my-work' },
            revision: 4,
            schemaVersion: 1,
            updatedAt: '2026-10-14T01:03:00Z',
          },
        },
        readAt: '2026-10-14T01:03:00Z',
      });
      putWorkspacePreferenceApi.mockRejectedValueOnce(
        conflict409({ currentRevision: 4, preferenceKey: 'default_home' }),
      );
      const ok2 = await setDefaultHomePreference('/my-work');
      expect(ok2).toBe(true);
      expect(saveStates.default_home).toBe('saved');
    });
  });

  describe('缓存隔离（部署地址+稳定用户 ID+权限版本）', () => {
    it('双 userId 缓存 key 不同；同 key 写入互不可见', async () => {
      setAccount('user-a');
      const keyA = currentPreferenceCacheKey();
      const a = usePagePreference('page-iso');
      a.updateColumns([{ key: 'c1', label: 'C1', visible: true }]);
      await nextTick();
      expect(localStorage.getItem(keyA)).toContain('c1');

      setAccount('user-b');
      const keyB = currentPreferenceCacheKey();
      expect(keyB).not.toBe(keyA);
      expect(keyA).toContain('user-a');
      expect(keyB).toContain('user-b');
      // B 的缓存槽位为空（A 的写入对 B 不可见）
      expect(localStorage.getItem(keyB)).toBeNull();
    });

    it('权限版本：撤权（权限码变化）→ 缓存 key 变化', () => {
      setAccount('user-a', ['IC_ENGINEER'], ['read:a', 'write:b']);
      const before = currentPreferenceCacheKey();
      setAccount('user-a', ['IC_ENGINEER'], ['read:a']);
      const after = currentPreferenceCacheKey();
      expect(after).not.toBe(before);
    });

    it('换账号登录 → 清理本部署账户缓存并重置模块状态；退出登录 → 同样清理', async () => {
      setAccount('user-a');
      noteClpmAccountLogin('user-a'); // 模拟 A 登录登记
      const keyA = currentPreferenceCacheKey();
      const a = usePagePreference('page-switch');
      a.updateColumns([{ key: 'c9', label: 'C9', visible: true }]);
      await nextTick();
      expect(localStorage.getItem(keyA)).toContain('c9');

      // 换账号：noteClpmAccountLogin('user-b') 清理 A 的缓存
      noteClpmAccountLogin('user-b');
      expect(localStorage.getItem(keyA)).toBeNull();

      setAccount('user-b');
      const b = usePagePreference('page-switch');
      expect(b.preferences.value.columns).toBeUndefined();

      // 退出登录：缓存与状态清理
      const keyB = currentPreferenceCacheKey();
      b.updateColumns([{ key: 'c1', label: 'C1', visible: false }]);
      await nextTick();
      expect(localStorage.getItem(keyB)).toContain('c1');
      handleClpmAccountLogout();
      expect(localStorage.getItem(keyB)).toBeNull();
    });
  });

  describe('匿名 clpm-preferences 显式一次性导入', () => {
    const LEGACY = {
      preferences: {
        'loop-monitor': {
          savedFilters: [
            {
              createdAt: '2026-08-01T00:00:00Z',
              filters: { grade: 'POOR' },
              id: 'p1',
              name: '差回路',
              page: 'loop-monitor',
            },
          ],
          tableDensity: 'default',
        },
      },
      updatedAt: '2026-08-01T00:00:00Z',
      version: '1.0',
    };

    it('不自动导入：旧值存在时新实例不含旧筛选预设', () => {
      setAccount('user-a');
      localStorage.setItem('clpm-preferences', JSON.stringify(LEGACY));
      const summary = detectLegacyAnonymousPreferences();
      expect(summary?.filterPresetCount).toBe(1);
      // 新代码不读取旧匿名键（不自动归给当前账号）
      const p = usePagePreference('loop-monitor');
      expect(p.preferences.value.savedFilters ?? []).toHaveLength(0);
    });

    it('显式导入：逐 key PUT 服务端校验生效后移除旧键；忽略则旧值不再可用', async () => {
      setAccount('user-a');
      localStorage.setItem('clpm-preferences', JSON.stringify(LEGACY));
      putWorkspacePreferenceApi.mockResolvedValueOnce({
        payload: {
          'loop-monitor': [
            {
              createdAt: '2026-08-01T00:00:00Z',
              filters: '{"grade":"POOR"}',
              id: 'p1',
              name: '差回路',
              page: 'loop-monitor',
            },
          ],
        },
        preferenceKey: 'filter_presets',
        replayed: false,
        revision: 1,
        schemaVersion: 1,
        updatedAt: '2026-10-14T01:05:00Z',
      });
      const res = await importLegacyAnonymousPreferences();
      expect(res).toEqual({ failed: 0, imported: 1, saved: true, skipped: 0 });
      // 逐 key PUT（filter_presets 单 key，含序列化预设）
      const [key, body] = putWorkspacePreferenceApi.mock.calls[0]!;
      expect(key).toBe('filter_presets');
      expect(body.expectedRevision).toBe(0);
      expect(body.payload['loop-monitor']).toHaveLength(1);
      expect(body.payload['loop-monitor'][0].filters).toBe('{"grade":"POOR"}');
      expect(typeof body.requestId).toBe('string');
      // 服务端确认后旧键移除（不再提示/不再可重复导入）
      expect(localStorage.getItem('clpm-preferences')).toBeNull();
      expect(detectLegacyAnonymousPreferences()).toBeNull();

      // 忽略路径：旧键移除且不产生任何服务端写
      localStorage.setItem('clpm-preferences', JSON.stringify(LEGACY));
      dismissLegacyAnonymousPreferences();
      expect(localStorage.getItem('clpm-preferences')).toBeNull();
      expect(putWorkspacePreferenceApi).toHaveBeenCalledTimes(1);
    });

    it('导入保存失败：旧键保留可重试，不标记成功', async () => {
      setAccount('user-a');
      localStorage.setItem('clpm-preferences', JSON.stringify(LEGACY));
      putWorkspacePreferenceApi.mockRejectedValueOnce(
        new TypeError('Network Error'),
      );
      const res = await importLegacyAnonymousPreferences();
      expect(res.saved).toBe(false);
      expect(res.failed).toBe(1);
      expect(localStorage.getItem('clpm-preferences')).not.toBeNull();
      const { saveStates } = useWorkspacePreferenceState();
      expect(saveStates.filter_presets).toBe('unsaved');
    });
  });

  describe('偏好同步失败态（F-G1：不 reject、不阻断调用方）', () => {
    it('syncPreferencesFromServer 失败：正常返回且 serverSyncFailed=true；下次成功恢复', async () => {
      setAccount('user-a');
      getWorkspacePreferencesApi.mockRejectedValueOnce(
        new TypeError('Network Error'),
      );
      // 永不 reject（页面 mount 不被闸断）
      await expect(syncPreferencesFromServer()).resolves.toBeUndefined();
      const { serverLoaded, serverSyncFailed } =
        useWorkspacePreferenceState();
      expect(serverSyncFailed.value).toBe(true);
      expect(serverLoaded.value).toBe(false);

      getWorkspacePreferencesApi.mockResolvedValueOnce({
        items: {},
        readAt: '2026-10-14T02:00:00Z',
      });
      await syncPreferencesFromServer();
      expect(serverSyncFailed.value).toBe(false);
      expect(serverLoaded.value).toBe(true);
    });
  });

  describe('fetchDefaultHomePreference（F-G2 登录消费链）', () => {
    it('服务端偏好为合法站内路径 → 返回该路径', async () => {
      getWorkspacePreferencesApi.mockResolvedValueOnce({
        items: {
          default_home: {
            payload: { path: '/my-work' },
            revision: 1,
            schemaVersion: 1,
            updatedAt: '2026-10-14T01:00:00Z',
          },
        },
        readAt: '2026-10-14T01:00:00Z',
      });
      await expect(fetchDefaultHomePreference()).resolves.toBe('/my-work');
    });

    it('无偏好/非法值 → null（调用方回退角色映射）', async () => {
      // 无偏好
      getWorkspacePreferencesApi.mockResolvedValueOnce({
        items: {},
        readAt: '2026-10-14T01:00:00Z',
      });
      await expect(fetchDefaultHomePreference()).resolves.toBeNull();
      // 非法值逐个（协议外路径/绝对 URL/根路径/超长/非字符串）
      for (const bad of [
        '//evil.com/x',
        'http://evil.com',
        '/',
        `/${'a'.repeat(201)}`,
        42,
      ]) {
        getWorkspacePreferencesApi.mockResolvedValueOnce({
          items: {
            default_home: {
              payload: { path: bad },
              revision: 1,
              schemaVersion: 1,
              updatedAt: '2026-10-14T01:00:00Z',
            },
          },
          readAt: '2026-10-14T01:00:00Z',
        });
        await expect(fetchDefaultHomePreference()).resolves.toBeNull();
      }
    });

    it('加载失败（断网/404/5xx）→ null 不阻断登录', async () => {
      getWorkspacePreferencesApi.mockRejectedValueOnce(
        new TypeError('Network Error'),
      );
      await expect(fetchDefaultHomePreference()).resolves.toBeNull();
    });

    it('isValidHomePath 形态边界', () => {
      expect(isValidHomePath('/my-work')).toBe(true);
      expect(isValidHomePath('/cockpit?tab=loops')).toBe(true);
      expect(isValidHomePath('/')).toBe(false);
      expect(isValidHomePath('//x')).toBe(false);
      expect(isValidHomePath('relative/path')).toBe(false);
      expect(isValidHomePath('javascript:alert(1)')).toBe(false);
      expect(isValidHomePath('')).toBe(false);
      expect(isValidHomePath(null)).toBe(false);
      expect(isValidHomePath(`/${'a'.repeat(200)}`)).toBe(false);
    });
  });
});
