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

/** 当前用户已授权页面码集合（P4-02：来自 /auth/me pageCodes，DB 授权矩阵下发） */
let grantedPageCodes: null | Set<string> = null;

/**
 * 记录当前用户已授权页面码（store/auth.ts 在 fetchUserInfo 后调用）。
 */
function setGrantedPageCodes(pages: string[]) {
  grantedPageCodes = new Set(pages);
}

/**
 * 按已授权页面码过滤路由树（P4-02：菜单按可达页生成）。
 *
 * - 携带 ``meta.permCode`` 的路由：码未授权 → 整棵子树移除（菜单/直链同步受限）
 * - 未携带 ``permCode`` 的路由（兼容 redirect、legacy 书签页）：仍由 authority 过滤
 * - 纯菜单父路由（无 component）在全部子路由被移除后一并移除——父菜单
 *   不残留不可达入口；父 redirect 可达性由子树过滤后 vben 默认取首个
 *   存留子路由保证
 * - 模块过滤（applyModuleFilter）与页面码过滤均为 AND 语义：模块禁用或
 *   页面未授权任一不满足即移除；与后端 get_effective_access 口径一致
 */
function filterTreeByPageCodes(
  tree: RouteRecordRaw[],
  granted: Set<string>,
): RouteRecordRaw[] {
  const result: RouteRecordRaw[] = [];
  for (const route of tree) {
    const permCode = route.meta?.permCode as string | undefined;
    if (permCode && !granted.has(permCode)) {
      continue;
    }
    const filtered: RouteRecordRaw = { ...route };
    if (route.children && route.children.length > 0) {
      filtered.children = filterTreeByPageCodes(
        route.children as RouteRecordRaw[],
        granted,
      );
      // 纯菜单父路由：子路由全部被移除 → 父路由不再可达，一并移除
      const isLayoutParent = !route.component;
      if (isLayoutParent && filtered.children.length === 0) {
        continue;
      }
    }
    result.push(filtered);
  }
  return result;
}

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
 * 在模块过滤基础上叠加页面码过滤（P4-02：DB 授权矩阵驱动菜单可达页）。
 * 由路由守卫在 applyModuleFilter 后调用；未设置 pageCodes（旧后端兼容）时不过滤。
 */
function applyPagePermFilter() {
  if (!grantedPageCodes) {
    return;
  }
  const filtered = filterTreeByPageCodes(accessRoutes, grantedPageCodes);
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
  applyPagePermFilter,
  coreRouteNames,
  filterTreeByPageCodes,
  isKnownRoutePath,
  routes,
  setGrantedPageCodes,
};
