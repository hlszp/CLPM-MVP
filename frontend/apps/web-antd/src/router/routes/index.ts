import type { RouteRecordRaw } from 'vue-router';

import { mergeRouteModules, traverseTreeValues } from '@vben/utils';

import { coreRoutes, fallbackNotFoundRoute } from './core';

const dynamicRouteFiles = import.meta.glob('./modules/**/*.ts', {
  eager: true,
});

// 有需要可以自行打开注释，并创建文件夹
// const externalRouteFiles = import.meta.glob('./external/**/*.ts', { eager: true });
// const staticRouteFiles = import.meta.glob('./static/**/*.ts', { eager: true });

/** 全部动态路由（模块过滤前） */
const allDynamicRoutes: RouteRecordRaw[] = mergeRouteModules(dynamicRouteFiles);

/**
 * 业务路由默认以 path（而非 fullPath）作为标签栏 key。
 *
 * vben 默认 fullPathKey=true：标签 key 取 fullPath（含 query）。CLPM 以 URL
 * query 承载筛选/定位（useMonitorContext、?loopId= 等），同一菜单页从不同
 * 入口带不同 query 进入时 fullPath 不同 → 被当作“另一页”重复开同名标签；
 * 页内换筛选（router.replace 更新 query）同样会克隆标签。
 *
 * 统一默认 fullPathKey=false → key 退化为 path：同 path 页面无论 query 如何
 * 都合并为同一标签，点击左侧菜单即导航到已打开的页面，不再重复新开。
 * 个别路由如需“同 path 多开”，显式设置 meta.fullPathKey = true 即可。
 */
function setDefaultFullPathKey(tree: RouteRecordRaw[]): void {
  for (const route of tree) {
    const meta = route.meta as undefined | { fullPathKey?: boolean };
    // 未显式声明时默认以 path 作为标签 key；个别需多开的路由可显式 true
    if (meta && !('fullPathKey' in meta)) {
      meta.fullPathKey = false;
    }
    if (route.children && route.children.length > 0) {
      setDefaultFullPathKey(route.children as RouteRecordRaw[]);
    }
  }
}
setDefaultFullPathKey(allDynamicRoutes);

// const externalRoutes: RouteRecordRaw[] = mergeRouteModules(externalRouteFiles);
// const staticRoutes: RouteRecordRaw[] = mergeRouteModules(staticRoutes);
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

/**
 * 按启用模块过滤路由树。
 *
 * - 父路由 meta.module 命中禁用模块 → 整棵子树移除
 * - 无 meta.module 的路由（兼容路由 alert/loop/task）保留
 * - 递归过滤子路由
 */
function filterTreeByModules(
  tree: RouteRecordRaw[],
  enabledKeys: Set<string>,
): RouteRecordRaw[] {
  const result: RouteRecordRaw[] = [];
  for (const route of tree) {
    const moduleKey = route.meta?.module as string | undefined;
    if (moduleKey && !enabledKeys.has(moduleKey)) {
      continue;
    }
    const filtered: RouteRecordRaw = { ...route };
    if (route.children && route.children.length > 0) {
      filtered.children = filterTreeByModules(
        route.children as RouteRecordRaw[],
        enabledKeys,
      );
    }
    result.push(filtered);
  }
  return result;
}

/** 按模块过滤后的动态路由（登录拉取模块状态后更新） */
let accessRoutes: RouteRecordRaw[] = [...allDynamicRoutes, ...staticRoutes];

/** 已过滤的动态路由 path 模式（供 isKnownRoutePath 使用） */
let dynamicRoutePathPatterns: string[] = traverseTreeValues(
  allDynamicRoutes,
  (route) => route.path,
).filter(Boolean);

/**
 * 根据已启用模块 key 集合过滤路由树。
 * 由路由守卫在登录后、generateAccess 前调用。
 */
function applyModuleFilter(enabledKeys: Set<string>) {
  const filtered = filterTreeByModules(allDynamicRoutes, enabledKeys);
  accessRoutes = [...filtered, ...staticRoutes];
  dynamicRoutePathPatterns = traverseTreeValues(
    filtered,
    (route) => route.path,
  ).filter(Boolean);
}

/**
 * 判断路径是否存在于过滤后的路由表（未启用模块的 URL 返回 404 非 403）。
 */
function isKnownRoutePath(path: string): boolean {
  return dynamicRoutePathPatterns.some((pattern) => {
    const regex = new RegExp(`^${pattern.replaceAll(/:[^/]+/g, '[^/]+')}$`);
    return regex.test(path);
  });
}

export {
  accessRoutes,
  applyModuleFilter,
  coreRouteNames,
  isKnownRoutePath,
  routes,
};
