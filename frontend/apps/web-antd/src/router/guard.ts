import type { Router } from 'vue-router';

import { LOGIN_PATH } from '@vben/constants';
import { preferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';
import { startProgress, stopProgress } from '@vben/utils';

import { checkAuthzOnNavigate } from '#/composables/use-authz-live';
import { fetchModules, getEnabledKeys } from '#/composables/use-modules';
import {
  accessRoutes,
  applyModuleFilter,
  applyPagePermFilter,
  coreRouteNames,
} from '#/router/routes';
import { useAuthStore } from '#/store';

import { generateAccess } from './access';

/**
 * 通用守卫配置
 * @param router
 */
function setupCommonGuard(router: Router) {
  // 记录已经加载的页面
  const loadedPaths = new Set<string>();

  router.beforeEach((to) => {
    to.meta.loaded = loadedPaths.has(to.path);

    // 页面加载进度条
    if (!to.meta.loaded && preferences.transition.progress) {
      startProgress();
    }
    return true;
  });

  router.afterEach((to) => {
    // 记录页面是否加载,如果已经加载，后续的页面切换动画等效果不在重复执行

    loadedPaths.add(to.path);

    // 关闭页面加载进度条
    if (preferences.transition.progress) {
      stopProgress();
    }
  });
}

/**
 * 权限访问守卫配置
 * @param router
 */
function setupAccessGuard(router: Router) {
  router.beforeEach(async (to, from) => {
    const accessStore = useAccessStore();
    const userStore = useUserStore();
    const authStore = useAuthStore();

    // 基本路由，这些路由不需要进入权限拦截
    if (coreRouteNames.includes(to.name as string)) {
      if (to.path === LOGIN_PATH && accessStore.accessToken) {
        return decodeURIComponent(
          (to.query?.redirect as string) ||
            userStore.userInfo?.homePath ||
            preferences.app.defaultHomePath,
        );
      }
      return true;
    }

    // accessToken 检查
    if (!accessStore.accessToken) {
      // 明确声明忽略权限访问权限，则可以访问
      if (to.meta.ignoreAccess) {
        return true;
      }

      // 没有访问权限，跳转登录页面
      if (to.fullPath !== LOGIN_PATH) {
        return {
          path: LOGIN_PATH,
          // 如不需要，直接删除 query
          query:
            to.fullPath === preferences.app.defaultHomePath
              ? {}
              : { redirect: encodeURIComponent(to.fullPath) },
          // 携带当前跳转的页面，登录后重新跳转该页面
          replace: true,
        };
      }
      return to;
    }

    // 是否已经生成过动态路由
    if (accessStore.isAccessChecked) {
      // P4-04 在线生效：导航前节流校验策略 revision（最低下一次路由前刷新）。
      // revision 未变返回 null 放行；变更则重建路由表——失权目标路由已移除
      // （直链落入 404 兜底），来源页失权时返回撤离 Location。
      const authzRedirect = await checkAuthzOnNavigate();
      if (authzRedirect) {
        return authzRedirect;
      }
      return true;
    }

    // 验证 accessToken 是否有效并获取用户信息（防止 token 过期但仍在 localStorage 中）
    let userInfo;
    try {
      userInfo = await authStore.fetchUserInfo();
    } catch (error) {
      // 仅认证失效才清会话；网络超时/503/断连属于瞬态故障，清 token 会把
      // 「一次抖动」放大成「无法登录」。瞬态错误保留会话并中止本次导航。
      const err = error as {
        code?: string;
        response?: { data?: { code?: string }; status?: number };
      };
      const status = err?.response?.status;
      const code = err?.response?.data?.code ?? err?.code;
      const isAuthFailure =
        status === 401 ||
        code === 'ERR_TOKEN_INVALID' ||
        code === 'ERR_TOKEN_EXPIRED' ||
        code === 'ERR_UNAUTHORIZED' ||
        code === 'ERR_INVALID_TOKEN';
      if (!isAuthFailure) {
        console.error('fetchUserInfo transient failure, keep session', error);
        return false;
      }
      // token 无效或过期，清空并跳转登录页
      accessStore.setAccessToken(null);
      accessStore.setRefreshToken(null);
      return {
        path: LOGIN_PATH,
        query: { redirect: encodeURIComponent(to.fullPath) },
        replace: true,
      };
    }

    // 生成路由表
    // 当前登录用户拥有的角色标识列表
    const userRoles = userInfo.roles ?? [];

    // 拉取模块启用状态并过滤路由树
    // （先模块过滤 → 再页面码过滤 → 最后 authority 过滤，三层 AND 语义）
    await fetchModules();
    applyModuleFilter(new Set(getEnabledKeys()));
    // P4-02：页面码过滤（fetchUserInfo 已写入 pageCodes——DB 授权矩阵下发，
    // 菜单按可达页生成；meta.permCode 未授权的路由整棵移除，直链随路由表移除受限）
    applyPagePermFilter();

    // 生成菜单和路由
    const { accessibleMenus, accessibleRoutes } = await generateAccess({
      roles: userRoles,
      router,
      // 则会在菜单中显示，但是访问会被重定向到403
      routes: accessRoutes,
    });

    // 保存菜单信息和路由信息
    accessStore.setAccessMenus(accessibleMenus);
    accessStore.setAccessRoutes(accessibleRoutes);
    accessStore.setIsAccessChecked(true);
    const redirectPath = (from.query.redirect ??
      (to.path === preferences.app.defaultHomePath
        ? userInfo.homePath || preferences.app.defaultHomePath
        : to.fullPath)) as string;

    return {
      ...router.resolve(decodeURIComponent(redirectPath)),
      replace: true,
    };
  });
}

/**
 * 项目守卫配置
 * @param router
 */
function createRouterGuard(router: Router) {
  /** 通用 */
  setupCommonGuard(router);
  /** 权限访问 */
  setupAccessGuard(router);
}

export { createRouterGuard };
