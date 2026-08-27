import type { Recordable, UserInfo } from '@vben/types';

import { ref } from 'vue';
import { useRouter } from 'vue-router';

import { LOGIN_PATH } from '@vben/constants';
import { preferences } from '@vben/preferences';
import { resetAllStores, useAccessStore, useUserStore } from '@vben/stores';

import { notification } from 'ant-design-vue';
import { defineStore } from 'pinia';

import { getAccessCodesApi, getUserInfoApi, loginApi, logoutApi } from '#/api';
import { $t } from '#/locales';

/**
 * 角色默认首页（实现契约 §5 + UI/UX §4.2 三方对齐基准）
 *
 * FP-P0-08：后端 auth.py ROLE_DEFAULT_HOME 已对齐本表，事实源统一。
 * 角色映射优先于后端 defaultHome 返回值（双保险），二者口径一致。
 *
 * - EXPERT：仅诊断中心 + 回路整定 → /diagnosis/records（诊断记录）
 * - SPONSOR：仅汇总视图 → /reports/overview（统计报告总览）
 * - 其余角色 → /dashboard
 */
const ROLE_DEFAULT_HOME: Record<string, string> = {
  ADMIN: '/dashboard',
  EXPERT: '/diagnosis/records',
  IC_ENGINEER: '/dashboard',
  PE_ENGINEER: '/dashboard',
  SPONSOR: '/reports/overview',
};

/**
 * 计算用户默认首页：前端角色映射优先，回退后端 defaultHome，最终兜底 /dashboard
 */
function resolveHomePath(
  role: string,
  backendDefaultHome?: null | string,
): string {
  return ROLE_DEFAULT_HOME[role] ?? backendDefaultHome ?? '/dashboard';
}

export { resolveHomePath, ROLE_DEFAULT_HOME };

export const useAuthStore = defineStore('auth', () => {
  const accessStore = useAccessStore();
  const userStore = useUserStore();
  const router = useRouter();

  const loginLoading = ref(false);

  /**
   * 异步处理登录操作（对齐 IDS v3.2 §5.1）
   * @param params 登录表单数据（username, password, rememberMe）
   * @param onSuccess 登录成功回调（可选）
   */
  async function authLogin(
    params: Recordable<any>,
    onSuccess?: () => Promise<void> | void,
  ) {
    let userInfo: null | UserInfo = null;
    try {
      loginLoading.value = true;

      // 1. 调用登录接口，获取 accessToken + refreshToken + 基础用户信息
      const loginResult = await loginApi({
        password: params.password,
        rememberMe: params.rememberMe ?? false,
        username: params.username,
      });

      const { accessToken, refreshToken, user: loginUser } = loginResult;

      if (accessToken) {
        // 2. 存储 accessToken 和 refreshToken（持久化到 localStorage）
        accessStore.setAccessToken(accessToken);
        accessStore.setRefreshToken(refreshToken);

        // 3. 获取完整用户信息（/auth/me），包含 permissions 和 defaultHome
        const [currentUser, accessCodes] = await Promise.all([
          getUserInfoApi(),
          fetchAccessCodes(loginUser.permissions),
        ]);

        // 4. 转换为框架 UserInfo 并存储（首页按角色映射，见 ROLE_DEFAULT_HOME）
        userInfo = {
          avatar: '',
          desc: currentUser.email,
          homePath: resolveHomePath(currentUser.role, currentUser.defaultHome),
          realName: currentUser.displayName,
          roles: [currentUser.role],
          token: accessToken,
          userId: currentUser.id,
          username: currentUser.username,
        };

        userStore.setUserInfo(userInfo);
        accessStore.setAccessCodes(accessCodes);

        // 5. 跳转首页或回调
        if (accessStore.loginExpired) {
          accessStore.setLoginExpired(false);
        } else {
          await (onSuccess
            ? onSuccess()
            : router.push(
                userInfo.homePath || preferences.app.defaultHomePath,
              ));
        }

        if (userInfo?.realName) {
          notification.success({
            description: `${$t('authentication.loginSuccessDesc')}:${userInfo?.realName}`,
            duration: 3,
            message: $t('authentication.loginSuccess'),
          });
        }
      }
    } finally {
      loginLoading.value = false;
    }

    return {
      userInfo,
    };
  }

  /**
   * 获取权限码
   * 优先使用登录返回的 permissions，若为空则回退到 /auth/codes 接口
   */
  async function fetchAccessCodes(permissions?: string[]): Promise<string[]> {
    if (permissions && permissions.length > 0) {
      return permissions;
    }
    try {
      return await getAccessCodesApi();
    } catch {
      return [];
    }
  }

    /**
   * 登出（对齐 IDS v3.2 §5.3）
   * 清空 Store + localStorage，跳转登录页
   *
   * - 仅在 accessToken 有效时调用后端 logout（黑名单 token）
   * - doReAuthenticate 触发登出时 token 已被清空，跳过后端调用避免 401 循环
   * - 增加 isLoggingOut CAS 锁，防止手动登出与 401 拦截器登出并发执行
   * - 状态清理与路由跳转统一放在 finally，失败场景下跳转必达（fail-safe）
   */
  async function logout(redirect: boolean = true) {
    const accessStore = useAccessStore();
    // CAS：已在登出流程中则直接返回，避免 resetAllStores / router.replace 竞争
    if (accessStore.isLoggingOut) {
      return;
    }
    accessStore.setIsLoggingOut(true);
    // 快照当前 token 与当前页面 fullPath，避免清理动作之后的读值不一致
    const hasToken = Boolean(accessStore.accessToken);
    const currentFullPath = router.currentRoute.value.fullPath;
    try {
      if (hasToken) {
        try {
          await logoutApi();
        } catch {
          // logout 接口失败（401 / 403 / 断网）不阻断本地登出
        }
      }
    } finally {
      try {
        resetAllStores();
      } catch {
        // 兜底：resetAllStores 若抛错，手动清 token，避免守卫反弹回首页
        accessStore.setAccessToken(null);
        accessStore.setRefreshToken(null);
        accessStore.setAccessCodes([]);
        try {
          useUserStore().setUserInfo(null);
        } catch {
          /* noop */
        }
      }
      accessStore.setLoginExpired(false);
      accessStore.setIsLoggingOut(false);
      const targetQuery = redirect
        ? { redirect: encodeURIComponent(currentFullPath) }
        : {};
      try {
        await router.replace({ path: LOGIN_PATH, query: targetQuery });
      } catch {
        // Vue Router 中断或守卫抛错时兜底硬跳转，保证登出必达
        const qs = redirect && currentFullPath
          ? `?redirect=${encodeURIComponent(currentFullPath)}`
          : '';
        window.location.replace(`${LOGIN_PATH}${qs}`);
      }
    }
  }

  /**
   * 获取用户信息（对齐 IDS v3.2 §5.4）
   * 用于路由守卫中按需刷新用户信息
   */
  async function fetchUserInfo() {
    const currentUser = await getUserInfoApi();
    const userInfo: UserInfo = {
      avatar: '',
      desc: currentUser.email,
      homePath: resolveHomePath(currentUser.role, currentUser.defaultHome),
      realName: currentUser.displayName,
      roles: [currentUser.role],
      token: accessStore.accessToken || '',
      userId: currentUser.id,
      username: currentUser.username,
    };
    userStore.setUserInfo(userInfo);
    accessStore.setAccessCodes(currentUser.permissions || []);
    return userInfo;
  }

  function $reset() {
    loginLoading.value = false;
  }

  return {
    $reset,
    authLogin,
    fetchUserInfo,
    loginLoading,
    logout,
  };
});
