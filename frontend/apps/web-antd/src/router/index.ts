import {
  createRouter,
  createWebHashHistory,
  createWebHistory,
} from 'vue-router';

import { resetStaticRoutes } from '@vben/utils';

import { createRouterGuard } from './guard';
import { routes } from './routes';

/**
 *  @zh_CN 创建vue-router实例
 */
const router = createRouter({
  history:
    import.meta.env.VITE_ROUTER_HISTORY === 'hash'
      ? createWebHashHistory(import.meta.env.VITE_BASE)
      : createWebHistory(import.meta.env.VITE_BASE),
  // 应该添加到路由的初始路由列表。
  routes,
  scrollBehavior: (to, _from, savedPosition) => {
    if (savedPosition) {
      return savedPosition;
    }
    return to.hash ? { behavior: 'smooth', el: to.hash } : { left: 0, top: 0 };
  },
  // 是否应该禁止尾部斜杠。
  // strict: true,
});

const resetRoutes = () => resetStaticRoutes(router, routes);

// 创建路由守卫
createRouterGuard(router);

export { resetRoutes, router };

// 公网路径间歇 503/传输中断会让路由懒加载 chunk 失败，Vue Router 会静默
// 中止导航（登录成功后停在登录页、工作台打不开）。捕获后整页重载目标路由，
// 用 sessionStorage 短窗防死循环。
const CHUNK_RELOAD_KEY = 'clpm_chunk_reload_at';
router.onError((error, to) => {
  const msg = String((error as Error)?.message || error);
  if (!msg.includes('dynamically imported module')) {
    return;
  }
  const now = Date.now();
  const last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0);
  if (now - last < 15_000) {
    return;
  }
  sessionStorage.setItem(CHUNK_RELOAD_KEY, String(now));
  const target = to?.fullPath;
  if (target && target !== '/' && !target.startsWith('/auth')) {
    window.location.replace(target);
  } else {
    window.location.reload();
  }
});
