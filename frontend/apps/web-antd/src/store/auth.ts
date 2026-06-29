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

        // 4. 转换为框架 UserInfo 并存储
        userInfo = {
          avatar: '',
          desc: currentUser.email,
          homePath: currentUser.defaultHome || '/dashboard',
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
   * 仅在 accessToken 有效时调用后端 logout（黑名单 token）。
   * doReAuthenticate 触发登出时 token 已被清空，跳过后端调用避免 401 循环。
   */
  async function logout(redirect: boolean = true) {
    if (accessStore.accessToken) {
      try {
        await logoutApi();
      } catch {
        // 不做任何处理
      }
    }
    resetAllStores();
    accessStore.setLoginExpired(false);

    // 回登录页带上当前路由地址
    await router.replace({
      path: LOGIN_PATH,
      query: redirect
        ? {
            redirect: encodeURIComponent(router.currentRoute.value.fullPath),
          }
        : {},
    });
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
      homePath: currentUser.defaultHome || '/dashboard',
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
