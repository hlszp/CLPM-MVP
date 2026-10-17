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
import { computed, reactive, readonly, ref } from 'vue';

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
/** 并发去重：读取在途时后续调用复用同一 Promise */
let loadPromise: null | Promise<boolean> = null;

const LIST_PAGE_SIZE = 100; // 后端上限（Query le=100）

export interface FollowToggleOutcome {
  /** 重试一次后仍冲突：附服务端当前关注态（UI 如实提示"已在别处变更"） */
  conflict?: { isFollowed: boolean | null };
  ok: boolean;
}

/** 拉取关注集全量（分页循环）；force=false 且已就绪时零请求直接返回 */
export function refreshFollows(force = false): Promise<boolean> {
  if (!force && loadState.value === 'ready') return Promise.resolve(true);
  if (loadPromise) return loadPromise;
  loadState.value = 'loading';
  loadPromise = (async () => {
    try {
      const next = new Map<string, number>();
      let page = 1;
      for (;;) {
        const data = await getMyLoopFollowsApi({
          page,
          pageSize: LIST_PAGE_SIZE,
        });
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
      loadState.value = 'error';
      return false;
    } finally {
      loadPromise = null;
    }
  })();
  return loadPromise;
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
    const result = await request(knownRevision ?? 0);
    applyLocal(loopId, result.isFollowed, result.revision);
    message.success(target ? `已关注 ${name}` : `已取消关注 ${name}`);
    return { ok: true };
  } catch (error) {
    const conflict = extractRevisionConflict(error);
    if (!conflict) {
      // 网络/5xx（setLoopFollowApi skipErrorMessage：全局不弹，由这里提示）
      applyLocal(loopId, wasFollowed, knownRevision ?? 0);
      message.error(target ? `关注 ${name} 失败，请重试` : `取消关注 ${name} 失败，请重试`);
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
      applyLocal(loopId, retry.isFollowed, retry.revision);
      message.success(target ? `已关注 ${name}` : `已取消关注 ${name}`);
      return { ok: true };
    } catch (retryError) {
      const c2 = extractRevisionConflict(retryError);
      // 以服务端真实状态回滚（不保留乐观假态），如实提示
      const serverFollowed = c2?.isFollowed ?? wasFollowed;
      applyLocal(loopId, serverFollowed, c2?.currentRevision ?? knownRevision ?? 0);
      message.warning(`关注状态已在其他页面变更，已按当前状态（${
        serverFollowed ? '已关注' : '未关注'
      }）刷新`);
      return { conflict: { isFollowed: c2?.isFollowed ?? null }, ok: false };
    }
  } finally {
    busy.delete(loopId);
  }
}

/** 重置模块级状态（测试隔离用） */
export function resetLoopFollowState(): void {
  followed.clear();
  busy.clear();
  loadState.value = 'idle';
  loadPromise = null;
}

/** 回路关注共享集访问器（三入口共用；状态本身为模块级单例） */
export function useLoopFollow() {
  return {
    /** 关注集读取状态（error=初始态不可信，入口应如实提示） */
    followLoadState: readonly(loadState),
    /** 当前关注回路 ID 集（快照；UI 响应式判定用 isFollowed） */
    followedLoopIds: computed(() => new Set(followed.keys())),
    /** ensureLoaded：未就绪时拉取一次（入口 onMounted 调用；失败返回 false） */
    ensureLoaded: () => refreshFollows(false),
    isBusy: (loopId: string) => busy.has(loopId),
    isFollowed: (loopId: string) => followed.has(loopId),
    refresh: () => refreshFollows(true),
    toggle: toggleLoopFollow,
  };
}
