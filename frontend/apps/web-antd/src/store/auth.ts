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
import { setGrantedPageCodes } from '#/router/routes';

/**
 * 角色默认首页（实现契约 §5 + UI/UX §4.2 三方对齐基准 + 方案 11 §3.1）
 *
 * FP-P0-08：后端 auth.py ROLE_DEFAULT_HOME 已对齐本表，事实源统一。
 * 角色映射优先于后端 defaultHome 返回值（双保险），二者口径一致。
 *
 * - 2026-10-14 P6-06（IAR-D1/D2）：工程师（IC/PE/EXPERT）首页为我的工作
 *   /my-work；管理员/观察者（ADMIN/SPONSOR）为驾驶舱 /cockpit。
 *   个人 default_home 偏好（/me/workspace-preferences，合法站内路径）优先
 *   于本角色默认——见 resolveUserHomePath。
 */
const ROLE_DEFAULT_HOME: Record<string, string> = {
  ADMIN: '/cockpit',
  EXPERT: '/my-work',
  IC_ENGINEER: '/my-work',
  PE_ENGINEER: '/my-work',
  SPONSOR: '/cockpit',
};

/**
 * 计算用户默认首页：前端角色映射优先，回退后端 defaultHome，最终兜底 /cockpit
 */
function resolveHomePath(
  role: string,
  backendDefaultHome?: null | string,
): string {
  return ROLE_DEFAULT_HOME[role] ?? backendDefaultHome ?? '/cockpit';
}

/**
 * 登录/首页解析（F-G2 default_home 消费链）：个人 default_home 偏好（服务端
 * 持久、合法站内路径）优先于角色映射；加载失败/无偏好/非法值回退角色映射，
 * **不阻断登录**。
 *
 * 时序：登录令牌与用户信息就绪后、router.push 前读取（避免"先跳角色首页
 * 再替换"的闪烁）；路径合法性经 isValidHomePath 双端校验，越权路径由路由
 * 守卫按既有口径兜底（与书签/旧链接一致）。
 */
async function resolveUserHomePath(
  role: string,
  backendDefaultHome?: null | string,
): Promise<string> {
  const roleHome = resolveHomePath(role, backendDefaultHome);
  try {
    const { fetchDefaultHomePreference, isValidHomePath } =
      await import('#/composables/use-clpm-preferences');
    const preferred = await fetchDefaultHomePreference();
    return preferred && isValidHomePath(preferred) ? preferred : roleHome;
  } catch {
    return roleHome;
  }
}

export { resolveHomePath, resolveUserHomePath, ROLE_DEFAULT_HOME };

/** 动态导入避免 auth → personal-work/request → store 初始化环。 */
async function clearLoopFollowSession(): Promise<void> {
  const { resetLoopFollowState } =
    await import('#/composables/use-loop-follow');
  resetLoopFollowState();
}

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
        // F07：更换 token 前使上一会话失效（包括同 userId 重新登录）。
        // 旧请求不能在 /auth/me 尚未返回时拿新 token 重试个人关注写入。
        await clearLoopFollowSession();
        // 2. 存储 accessToken 和 refreshToken（持久化到 localStorage）
        accessStore.setAccessToken(accessToken);
        accessStore.setRefreshToken(refreshToken);

        // 3. 获取完整用户信息（/auth/me），包含 permissions/pageCodes/defaultHome
        //    P4-02：permissions=动作码（按钮显隐）、pageCodes=页面码（菜单可达页）
        //    均由后端 DB 授权矩阵下发（显式码，无通配）
        const [currentUser, accessCodes] = await Promise.all([
          getUserInfoApi(),
          fetchAccessCodes(loginUser.permissions),
        ]);

        // 页面码写入路由表模块（守卫在 generateAccess 前消费）
        setGrantedPageCodes(currentUser.pageCodes ?? []);

        // 4. 转换为框架 UserInfo 并存储（首页：个人 default_home 偏好优先于
        //    角色映射；router.push 前解析完毕，无闪烁）
        userInfo = {
          avatar: '',
          desc: currentUser.email,
          homePath: await resolveUserHomePath(
            currentUser.role,
            currentUser.defaultHome,
          ),
          realName: currentUser.displayName,
          roles: [currentUser.role],
          token: accessToken,
          userId: currentUser.id,
          username: currentUser.username,
        };

        userStore.setUserInfo(userInfo);
        accessStore.setAccessCodes(accessCodes);

        // P6-06：登记本次登录稳定用户 ID——与上次登录不同（换账号）时清理
        // 本部署账户维度偏好缓存（上一个人的内容不能展示给下一个人）。
        // 动态 import 避免 auth → composables → api/request → store 初始化环。
        try {
          const { noteClpmAccountLogin } =
            await import('#/composables/use-clpm-preferences');
          noteClpmAccountLogin(String(currentUser.id));
        } catch {
          /* 缓存清理失败不阻断登录 */
        }

        // 5. 跳转首页或回调
        if (accessStore.loginExpired) {
          accessStore.setLoginExpired(false);
        } else {
          await (onSuccess
            ? onSuccess()
            : Promise.resolve(
                router.push(
                  userInfo.homePath || preferences.app.defaultHomePath,
                ),
              ).catch((navError: unknown) => {
                // 懒加载 chunk 失败会由 router.onError 整页重载；若重载
                // 被防死循环拦截，至少让用户知道不是账密问题。
                console.error('post-login navigation failed', navError);
                notification.error({
                  description: '首页资源加载失败，将自动重试；仍失败请刷新页面',
                  duration: 5,
                  message: '页面加载失败',
                });
              }));
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
      // isLoggingOut 的同步订阅已经清空已加载单例；动态导入兜底未加载模块。
      // 关注清理失败也不能阻断退出，finally 仍负责清 token 与跳转。
      try {
        await clearLoopFollowSession();
      } catch {
        /* noop */
      }
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
      // P6-06：退出清理账户维度偏好缓存与模块状态（服务端数据不受影响；
      // 换设备/换账号/清理浏览器后由服务端恢复，不在本机残留上一个人内容）。
      try {
        const { handleClpmAccountLogout } =
          await import('#/composables/use-clpm-preferences');
        handleClpmAccountLogout();
      } catch {
        /* 缓存清理失败不阻断登出 */
      }
      const targetQuery = redirect
        ? { redirect: encodeURIComponent(currentFullPath) }
        : {};
      try {
        await router.replace({ path: LOGIN_PATH, query: targetQuery });
      } catch {
        // Vue Router 中断或守卫抛错时兜底硬跳转，保证登出必达
        const qs =
          redirect && currentFullPath
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
      homePath: await resolveUserHomePath(
        currentUser.role,
        currentUser.defaultHome,
      ),
      realName: currentUser.displayName,
      roles: [currentUser.role],
      token: accessStore.accessToken || '',
      userId: currentUser.id,
      username: currentUser.username,
    };
    userStore.setUserInfo(userInfo);
    accessStore.setAccessCodes(currentUser.permissions || []);
    // P4-02：页面码随用户信息刷新（守卫菜单过滤消费；无值时不过滤=旧后端兼容）
    setGrantedPageCodes(currentUser.pageCodes ?? []);
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
