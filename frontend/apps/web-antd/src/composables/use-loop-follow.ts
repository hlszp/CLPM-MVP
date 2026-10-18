/**
 * 回路关注共享状态（P6-05 关注入口断链修复）
 *
 * 修复背景：follow API（PUT /me/loop-follows/{loopId}）此前前端零调用，
 * 用户无法从任何界面发起关注，「我的关注」视图结构性为空。本 composable
 * 为三个入口（回路监视列表星标 / wb360 页头星标 / 我的工作-我的关注）
 * 提供同一份关注集与切换动作，复用 api/personal-work.ts 既有封装，
 * 不分叉第二套实现。
 *
 * 后端契约（personal_workspace.py，树内源码即事实）：
 * - GET /me/loop-follows 仅返回本人已关注且可访问回路（分页，pageSize≤100）
 *   ——未在清单中的回路视为未关注（含"关注后又取消"的墓碑行）；
 * - PUT 设定语义非 toggle：首次创建（含墓碑复活）expectedRevision 失配即
 *   409，携 {currentRevision, isFollowed}；同 requestId 幂等重放；
 * - 取消关注保留版本墓碑，取消后迟到的旧关注请求（旧版本号或
 *   expectedRevision=0）均 409，不得复活。
 *
 * 客户端纪律（01a §2.1/§3）：乐观更新仅在服务端确认后保留；失败回滚；
 * 409 按冲突载荷收敛——服务端已是目标状态视为幂等成功，否则以
 * currentRevision 重试一次，再冲突则以服务端真实状态回滚并如实提示。
 */
import { computed, reactive, ref } from 'vue';

import { useAccessStore, useUserStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import {
  extractRevisionConflict,
  getMyLoopFollowsApi,
  newRequestId,
  setLoopFollowApi,
} from '#/api/personal-work';

/* ── 模块级状态（全应用共享一份：监视列表/wb360 页头/我的工作同一关注集；
先例 use-clpm-preferences 同款模块级单例模式） ── */

/** loopId → 当前已知 revision（仅关注中的回路；未关注视为 0/未知） */
const followed = reactive(new Map<string, number>());
/** 关注集读取状态：idle=未读取 loading/ready/error（error 时星标初始态不可信，如实提示） */
const loadState = ref<'error' | 'idle' | 'loading' | 'ready'>('idle');
/** 单回路切换在途（防同回路重入；其他回路不受影响） */
const busy = reactive(new Set<string>());
/** 单例只归属于当前账户；同一 userId 退出重登也以 generation 隔开。 */
const ownerId = ref<null | string>(null);
let generation = 0;
interface FollowContext {
  generation: number;
  ownerId: string;
}
interface FollowLoad {
  context: FollowContext;
  promise: Promise<boolean>;
}
/** 并发去重只在相同账户与会话代际内复用。 */
let loadRequest: FollowLoad | null = null;
let userStore: null | ReturnType<typeof useUserStore> = null;
let accessStore: null | ReturnType<typeof useAccessStore> = null;
let stopAccountSubscriptions: Array<() => void> = [];

function currentOwner(): null | string {
  if (!accessStore?.accessToken || accessStore.isLoggingOut) return null;
  const id = userStore?.userInfo?.userId;
  return id ? String(id) : null;
}

function invalidateForOwner(nextOwner: null | string): void {
  generation += 1;
  followed.clear();
  busy.clear();
  loadState.value = 'idle';
  loadRequest = null;
  ownerId.value = nextOwner;
}

function syncAccountOwner(): void {
  const nextOwner = currentOwner();
  if (ownerId.value !== nextOwner) invalidateForOwner(nextOwner);
}

function ensureAccount(): void {
  try {
    const nextUserStore = useUserStore();
    const nextAccessStore = useAccessStore();
    if (userStore !== nextUserStore || accessStore !== nextAccessStore) {
      for (const stop of stopAccountSubscriptions) stop();
      userStore = nextUserStore;
      accessStore = nextAccessStore;
      invalidateForOwner(null);
      // detached：页面卸载后仍接收退出/换账户，不能让模块级状态失去清理入口。
      // sync：新账户的第一帧和旧请求的下一微任务之前就已失效。
      const options = { detached: true, flush: 'sync' as const };
      stopAccountSubscriptions = [
        userStore.$subscribe(syncAccountOwner, options),
        accessStore.$subscribe(syncAccountOwner, options),
      ];
    }
    syncAccountOwner();
  } catch {
    // 尚未安装 Pinia/未登录时没有可证明归属的关注集，禁止匿名请求。
    invalidateForOwner(null);
  }
}

function captureContext(): FollowContext | null {
  ensureAccount();
  return ownerId.value ? { generation, ownerId: ownerId.value } : null;
}

function isCurrent(context: FollowContext): boolean {
  return (
    context.generation === generation &&
    context.ownerId === ownerId.value &&
    context.ownerId === currentOwner()
  );
}

function hasCurrentOwner(): boolean {
  return ownerId.value !== null && ownerId.value === currentOwner();
}

const LIST_PAGE_SIZE = 100; // 后端上限（Query le=100）

export interface FollowToggleOutcome {
  /** 重试一次后仍冲突：附服务端当前关注态（UI 如实提示"已在别处变更"） */
  conflict?: { isFollowed: boolean | null };
  ok: boolean;
}

/** 拉取关注集全量（分页循环）；force=false 且已就绪时零请求直接返回 */
export function refreshFollows(force = false): Promise<boolean> {
  const context = captureContext();
  if (!context) return Promise.resolve(false);
  if (!force && loadState.value === 'ready') return Promise.resolve(true);
  if (loadRequest && isCurrent(loadRequest.context)) return loadRequest.promise;
  loadState.value = 'loading';
  const request: FollowLoad = { context, promise: Promise.resolve(false) };
  loadRequest = request;
  request.promise = (async () => {
    try {
      const next = new Map<string, number>();
      let page = 1;
      for (;;) {
        const data = await getMyLoopFollowsApi({
          page,
          pageSize: LIST_PAGE_SIZE,
        });
        // 旧账户的分页不能继续用新令牌读取，也不能把旧清单回灌新账户。
        if (!isCurrent(context)) return false;
        for (const it of data.items) next.set(it.loopId, it.revision);
        const size = data.pageSize || LIST_PAGE_SIZE;
        if (page >= Math.max(1, Math.ceil(data.total / size))) break;
        page += 1;
      }
      followed.clear();
      for (const [id, rev] of next) followed.set(id, rev);
      loadState.value = 'ready';
      return true;
    } catch {
      if (isCurrent(context)) loadState.value = 'error';
      return false;
    } finally {
      if (loadRequest === request) loadRequest = null;
    }
  })();
  return request.promise;
}

function applyLocal(loopId: string, isFollowed: boolean, revision: number) {
  if (isFollowed) followed.set(loopId, revision);
  else followed.delete(loopId);
}

/**
 * 关注状态切换（乐观更新 + 失败回滚 + 409 收敛）。
 * @param loopId 回路 ID
 * @param label 提示文案中的位号（可选，缺省"该回路"）
 */
export async function toggleLoopFollow(
  loopId: string,
  label: null | string = '该回路',
): Promise<FollowToggleOutcome> {
  const context = captureContext();
  if (!context) return { ok: false };
  const name = label;
  if (busy.has(loopId)) return { ok: false };
  busy.add(loopId);
  const wasFollowed = followed.has(loopId);
  const knownRevision = followed.get(loopId);
  const target = !wasFollowed;

  // 乐观更新：服务端确认后以返回 revision 收敛；失败路径回滚（占位 0 不外泄）
  applyLocal(loopId, target, 0);

  const request = (revision: number) =>
    setLoopFollowApi(loopId, {
      expectedRevision: revision,
      isFollowed: target,
      requestId: newRequestId(),
    });

  try {
    if (!isCurrent(context)) return { ok: false };
    const result = await request(knownRevision ?? 0);
    if (!isCurrent(context)) return { ok: false };
    applyLocal(loopId, result.isFollowed, result.revision);
    message.success(target ? `已关注 ${name}` : `已取消关注 ${name}`);
    return { ok: true };
  } catch (error) {
    // 409 重试也要在账户屏障内：否则旧请求会携新账户 token 再次写入。
    if (!isCurrent(context)) return { ok: false };
    const conflict = extractRevisionConflict(error);
    if (!conflict) {
      // 网络/5xx（setLoopFollowApi skipErrorMessage：全局不弹，由这里提示）
      applyLocal(loopId, wasFollowed, knownRevision ?? 0);
      message.error(
        target ? `关注 ${name} 失败，请重试` : `取消关注 ${name} 失败，请重试`,
      );
      return { ok: false };
    }
    // 幂等收敛：服务端已是目标状态（他处已操作/同义重放）→ 按成功口径对齐
    if (conflict.isFollowed === target) {
      applyLocal(loopId, target, conflict.currentRevision ?? 0);
      message.success(target ? `已关注 ${name}` : `已取消关注 ${name}`);
      return { ok: true };
    }
    // 版本失配（如本地 revision 过期/墓碑复活）：以服务端当前版本重试一次
    try {
      const retry = await request(conflict.currentRevision ?? 0);
      if (!isCurrent(context)) return { ok: false };
      applyLocal(loopId, retry.isFollowed, retry.revision);
      message.success(target ? `已关注 ${name}` : `已取消关注 ${name}`);
      return { ok: true };
    } catch (retryError) {
      if (!isCurrent(context)) return { ok: false };
      const c2 = extractRevisionConflict(retryError);
      // 以服务端真实状态回滚（不保留乐观假态），如实提示
      const serverFollowed = c2?.isFollowed ?? wasFollowed;
      applyLocal(
        loopId,
        serverFollowed,
        c2?.currentRevision ?? knownRevision ?? 0,
      );
      message.warning(
        `关注状态已在其他页面变更，已按当前状态（${
          serverFollowed ? '已关注' : '未关注'
        }）刷新`,
      );
      return { conflict: { isFollowed: c2?.isFollowed ?? null }, ok: false };
    }
  } finally {
    // A 的 finally 不能解除 B 对同一回路刚取得的防重入锁。
    if (isCurrent(context)) busy.delete(loopId);
  }
}

/** 账户/登录会话边界清理；同时让所有旧读取、切换和重试结果失效。 */
export function resetLoopFollowState(): void {
  invalidateForOwner(null);
}

/** 回路关注共享集访问器（三入口共用；状态本身为模块级单例） */
export function useLoopFollow() {
  ensureAccount();
  return {
    /** 关注集读取状态（error=初始态不可信，入口应如实提示） */
    followLoadState: computed(() =>
      hasCurrentOwner() ? loadState.value : 'idle',
    ),
    /** 当前关注回路 ID 集（快照；UI 响应式判定用 isFollowed） */
    followedLoopIds: computed(
      () => new Set(hasCurrentOwner() ? followed.keys() : []),
    ),
    /** ensureLoaded：未就绪时拉取一次（入口 onMounted 调用；失败返回 false） */
    ensureLoaded: () => refreshFollows(false),
    isBusy: (loopId: string) => hasCurrentOwner() && busy.has(loopId),
    isFollowed: (loopId: string) => hasCurrentOwner() && followed.has(loopId),
    refresh: () => refreshFollows(true),
    toggle: toggleLoopFollow,
  };
}
