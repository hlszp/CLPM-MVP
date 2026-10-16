/**
 * 授权策略在线生效（P4-04）
 *
 * 服务端即时校验由后端 authz_runtime 每请求 revision 比对独立保证
 * （"API 下一请求即时校验"）；本模块负责 UI 生效节奏（01§3.5 / 03§7 P4-04）：
 *
 * - 活跃会话轮询 /auth/effective-access（25s，≤30s 上限）比对 policyRevision；
 * - 路由导航时节流检查（最低下一次路由前刷新）；
 * - revision 变化 → 全量刷新：fetchUserInfo（accessCodes/pageCodes）→
 *   模块过滤 → 页面码过滤 → generateAccess 重建菜单与路由表；
 * - 失权撤离：当前路由不再可达时关闭下钻弹窗并跳转可访问入口；
 * - 失权清理：移除已注册但不再可达的动态路由并关闭对应标签页（标签按
 *   路由名匹配——含经顶级记录替换移除的子页面；首检基线为空时从当前
 *   路由表回填，整组失权的顶级路由同被移除——REWORK D1/D2）；
 *   只清授权相关状态——页面内订阅随组件卸载自然清理，不清用户无关工作
 *   （不 resetAllStores，保留筛选/密度等用户偏好）。
 *
 * 不以隐藏菜单代替授权：路由/按钮显隐由 DB 矩阵下发码驱动，服务端
 * require_perms 恒为最终判定（本模块仅对齐 UI 展示与实际 403）。
 */
import type { Router, RouteRecordRaw } from 'vue-router';

import { onBeforeUnmount, watch } from 'vue';

import { preferences } from '@vben/preferences';
import { useAccessStore, useTabbarStore, useUserStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import { getMyEffectiveAccessApi } from '#/api/authorization';
import { fetchModules, getEnabledKeys } from '#/composables/use-modules';
import { generateAccess } from '#/router/access';
import {
  accessRoutes,
  applyModuleFilter,
  applyPagePermFilter,
  coreRouteNames,
} from '#/router/routes';
import { useAuthStore } from '#/store';
import { closeDrillModals } from '#/views/cockpit/wb-comps/use-drill';

/** 轮询间隔（≤30s 上限，留余量） */
const POLL_INTERVAL_MS = 25_000;
/** 导航检查节流：同一窗口内不重复请求（避免高频跳转放大轮询） */
const NAV_THROTTLE_MS = 10_000;

let activeRouter: null | Router = null;
let pollTimer: null | ReturnType<typeof setInterval> = null;
/** 已知策略 revision（null=尚未建立基线，首次检查只记基线不刷新） */
let knownRevision: null | number = null;
let lastCheckAt = 0;
let inFlight: null | Promise<boolean> = null;
/** 本会话已注册的动态路由名（刷新前后 diff 出失权路由） */
let installedDynamicNames = new Set<string>();

function collectRouteNames(
  routes: RouteRecordRaw[],
  names: Set<string> = new Set(),
): Set<string> {
  for (const route of routes) {
    if (typeof route.name === 'string') names.add(route.name);
    if (route.children && route.children.length > 0) {
      collectRouteNames(route.children as RouteRecordRaw[], names);
    }
  }
  return names;
}

/**
 * 关闭失权路由对应的已打开标签（REWORK D2）。
 *
 * 按路由名匹配标签——tab.key 可能是含 query 的 fullPath，按路径反查
 * 不可靠（getTabKey：pageKey || fullPath || path）。当前激活的失权页
 * 跳过：由撤离路径在 push 兜底页后关闭，避免 closeTab 激活分支
 * "跳相邻标签"的内部导航与撤离目标竞态。
 */
function closeLostTabs(lostNames: Set<string>): void {
  const router = activeRouter;
  if (!router || lostNames.size === 0) return;
  const currentName = router.currentRoute.value.name;
  const tabbarStore = useTabbarStore();
  // 快照后遍历：closeTabByKey 过程中 tabs 数组变异，不边遍历边改
  const tabsSnapshot = [...tabbarStore.getTabs];
  for (const tab of tabsSnapshot) {
    const tabName = typeof tab.name === 'string' ? tab.name : null;
    if (tabName && lostNames.has(tabName) && tabName !== currentName) {
      void tabbarStore.closeTabByKey(String(tab.key ?? ''), router);
    }
  }
}

/**
 * 全量重建授权路由表并移除失权路由。
 *
 * generateAccessible 对同名路由更新/追加、不删除——失权路由必须显式
 * removeRoute，否则直链仍可达（不能只隐藏菜单）。
 */
async function rebuildAccessRoutes(): Promise<Set<string>> {
  const router = activeRouter;
  if (!router) return new Set();
  const accessStore = useAccessStore();
  const authStore = useAuthStore();

  // REWORK D1/D2：generateAccess 对同名顶级记录只更新/追加不删除，其
  // children 子树被整体替换——失权子页在替换后即从路由表查不到，因此
  // 基线与失权判定所需的路由名都必须在替换前采集：
  // - D1：首检基线为空（登录后未回填）时，用当前路由表的全部非 core
  //   路由名做首检基线——登录期注册、此后整组失权的顶级路由（空父剪枝
  //   后不再出现在新 accessibleRoutes）才能进入 removeRoute 分支，
  //   避免整个会话内直链可达失权页面壳；
  // - D2：子页面失权经顶级记录替换移除时 hasRoute=false，removeRoute
  //   跳过但标签关闭不依赖 hasRoute（lostNames 统一驱动）。
  const preNames = new Set<string>();
  for (const route of router.getRoutes()) {
    const name = typeof route.name === 'string' ? route.name : null;
    if (name && !coreRouteNames.includes(name)) {
      preNames.add(name);
    }
  }
  if (installedDynamicNames.size === 0) {
    installedDynamicNames = preNames;
  }

  const userInfo = await authStore.fetchUserInfo();
  await fetchModules();
  applyModuleFilter(new Set(getEnabledKeys()));
  applyPagePermFilter();

  const { accessibleMenus, accessibleRoutes } = await generateAccess({
    roles: userInfo.roles ?? [],
    router,
    routes: accessRoutes,
  });
  accessStore.setAccessMenus(accessibleMenus);
  accessStore.setAccessRoutes(accessibleRoutes);

  const names = collectRouteNames(accessibleRoutes);
  // 失权路由：上一轮基线有、本轮可达集无 → 移除（含直链）+ 关标签
  const lostNames = new Set<string>();
  for (const name of installedDynamicNames) {
    if (!names.has(name)) {
      lostNames.add(name);
      if (router.hasRoute(name)) {
        router.removeRoute(name);
      }
    }
  }
  installedDynamicNames = names;
  closeLostTabs(lostNames);
  return names;
}

/** 撤离目标：默认首页可达则用默认页，否则兜底驾驶舱 */
function resolveFallbackHome(names: Set<string>): string {
  const userStore = useUserStore();
  const home = userStore.userInfo?.homePath || preferences.app.defaultHomePath;
  if (activeRouter) {
    const resolved = activeRouter.resolve(home);
    const resolvedName = resolved?.name;
    if (
      typeof resolvedName === 'string' &&
      (names.has(resolvedName) || coreRouteNames.includes(resolvedName))
    ) {
      return home;
    }
  }
  return '/cockpit';
}

/**
 * 撤离失权当前页：关下钻弹窗（订阅随组件卸载自然清理）→ 提示 → 跳转。
 * 在轮询路径调用（导航路径由守卫消费 checkAuthzOnNavigate 的返回值）。
 */
async function evacuateCurrentRoute(names: Set<string>): Promise<void> {
  const router = activeRouter;
  if (!router) return;
  const current = router.currentRoute.value;
  const currentName = typeof current.name === 'string' ? current.name : null;
  const lost =
    !!currentName &&
    !coreRouteNames.includes(currentName) &&
    !names.has(currentName);
  if (!lost || !currentName) return;
  closeDrillModals();
  message.warning('授权已变更：当前页面不再可用，已返回可访问页面');
  await router.push(resolveFallbackHome(names));
  // REWORK D2：撤离后关闭失权当前页标签（此时已非激活，直接移除不触发
  // closeTab 激活分支的相邻标签跳转）
  closeLostTabs(new Set([currentName]));
}

/** 单次 revision 检查（节流 + in-flight 去重）；返回是否触发了刷新 */
async function checkOnce(force: boolean): Promise<boolean> {
  const router = activeRouter;
  if (!router) return false;
  const accessStore = useAccessStore();
  if (!accessStore.accessToken) return false;
  const now = Date.now();
  if (!force && now - lastCheckAt < NAV_THROTTLE_MS) return false;
  if (inFlight) return inFlight;
  lastCheckAt = now;
  inFlight = (async () => {
    try {
      const data = await getMyEffectiveAccessApi();
      if (knownRevision === null) {
        knownRevision = data.policyRevision;
        return false;
      }
      if (data.policyRevision === knownRevision) return false;
      knownRevision = data.policyRevision;
      const names = await rebuildAccessRoutes();
      await evacuateCurrentRoute(names);
      return true;
    } catch {
      // 网络/认证失败不阻塞导航；登录过期由拦截器统一处理
      return false;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

/**
 * 导航前节流检查（守卫消费）：revision 未变放行（null）；变更则重建路由表——
 * 目标页路由已随 removeRoute 移除，失权直链自然落入 404 兜底；来源页失权
 * 时返回撤离 Location（replace 语义）。
 */
export async function checkAuthzOnNavigate(): Promise<
  null | { path: string; replace: boolean }
> {
  const router = activeRouter;
  if (!router) return null;
  const accessStore = useAccessStore();
  if (!accessStore.accessToken || !accessStore.isAccessChecked) return null;
  const changed = await checkOnce(false);
  if (!changed) return null;
  const current = router.currentRoute.value;
  const names = installedDynamicNames;
  const currentName = typeof current.name === 'string' ? current.name : null;
  const lost =
    !!currentName &&
    !coreRouteNames.includes(currentName) &&
    !names.has(currentName);
  if (lost && currentName) {
    closeDrillModals();
    message.warning('授权已变更：当前页面不再可用，已返回可访问页面');
    // REWORK D2：先 push 撤离再关当前失权标签（非激活态直接移除）；
    // 随后返回同路径 Location（vue-router 对与当前相同的导航 no-op，
    // 仅用于终止守卫对失权目标 to 的放行）
    const fallback = resolveFallbackHome(names);
    await router.push(fallback);
    closeLostTabs(new Set([currentName]));
    return { path: fallback, replace: true };
  }
  message.info('授权策略已更新，菜单与按钮权限已刷新');
  return null;
}

/** 登录成功/登出后重置基线（下次检查只记基线不误触发刷新） */
export function resetAuthzLiveBaseline(): void {
  knownRevision = null;
  lastCheckAt = 0;
}

/** 布局挂载时注入 router（模块级无法调用 useRouter，由 basic.vue 传入） */
export function bindAuthzLiveRouter(router: Router): void {
  activeRouter = router;
}

/**
 * 布局级挂载（basic.vue setup 中调用）：按 token 起停轮询。
 *
 * - 有 token 时轮询（首次只建立基线）；token 清空（登出）自动停止并重置；
 * - 布局卸载清理定时器（不留悬挂 interval）。
 */
export function useAuthzLive(): void {
  const accessStore = useAccessStore();

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      void checkOnce(false);
    }, POLL_INTERVAL_MS);
  }

  watch(
    () => accessStore.accessToken,
    (token, oldToken) => {
      if (token && token !== oldToken) {
        // 会话/用户切换（登录、重登、换号）：重置基线，由首检建立新基线，
        // 不把前一用户的 revision 当作当前用户的比对锚
        resetAuthzLiveBaseline();
      }
      if (token) {
        startPolling();
        if (knownRevision === null) {
          void checkOnce(true);
        }
      } else {
        stopPolling();
        resetAuthzLiveBaseline();
      }
    },
    { immediate: true },
  );

  onBeforeUnmount(() => {
    stopPolling();
  });
}
