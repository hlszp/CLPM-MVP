import type { RouteRecordRaw } from 'vue-router';

import { mergeRouteModules, traverseTreeValues } from '@vben/utils';

import { coreRoutes, fallbackNotFoundRoute } from './core';

const dynamicRouteFiles = import.meta.glob('./modules/**/*.ts', {
  eager: true,
});

// 有需要可以自行打开注释，并创建文件夹
// const externalRouteFiles = import.meta.glob('./external/**/*.ts', { eager: true });
// const staticRouteFiles = import.meta.glob('./static/**/*.ts', { eager: true });

/** 动态路由 */
const dynamicRoutes: RouteRecordRaw[] = mergeRouteModules(dynamicRouteFiles);

/** 外部路由列表，访问这些页面可以不需要Layout，可能用于内嵌在别的系统(不会显示在菜单中) */
// const externalRoutes: RouteRecordRaw[] = mergeRouteModules(externalRouteFiles);
// const staticRoutes: RouteRecordRaw[] = mergeRouteModules(staticRouteFiles);
const staticRoutes: RouteRecordRaw[] = [];
const externalRoutes: RouteRecordRaw[] = [];

/** 路由列表，由基本路由、外部路由和404兜底路由组成
 *  无需走权限验证（会一直显示在菜单中） */
const routes: RouteRecordRaw[] = [
  ...coreRoutes,
  ...externalRoutes,
  fallbackNotFoundRoute,
];

/** 基本路由列表，这些路由不需要进入权限拦截 */
const coreRouteNames = traverseTreeValues(coreRoutes, (route) => route.name);

/** 有权限校验的路由列表，包含动态路由和静态路由 */
const accessRoutes = [...dynamicRoutes, ...staticRoutes];

/** 全部动态路由 path 模式（含 :param 参数段）——用于 404/403 语义区分（整改 C2-2） */
const dynamicRoutePathPatterns = traverseTreeValues(
  dynamicRoutes,
  (route) => route.path,
).filter((p): p is string => Boolean(p));

/**
 * 判断路径是否存在于完整路由表（不论当前角色是否可访问）。
 * 存在但被权限过滤 → 应渲染 403；不存在 → 404。
 */
export function isKnownRoutePath(path: string): boolean {
  return dynamicRoutePathPatterns.some((pattern) => {
    const regex = new RegExp(`^${pattern.replaceAll(/:[^/]+/g, '[^/]+')}$`);
    return regex.test(path);
  });
}

export { accessRoutes, coreRouteNames, routes };
