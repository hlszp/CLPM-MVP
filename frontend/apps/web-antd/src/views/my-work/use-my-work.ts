/**
 * 我的工作——数据层（P6-06 O06-NAV）
 *
 * 契约落点（01a §2.1/§3、IA 方案 §4、03 号文）：
 * - 四视图（分配给我/系统建议/我的关注/最近继续）统一经 GET /me/work
 *   跨来源查询；三分区互斥（先判等待 → 再判今日计划 → 其余现在可行动）
 *   由服务端 partition 判定，本层只分组展示，不重新判定；
 * - 空待办 ≠ 健康：本层不输出任何健康断言；来源失败如实显示
 *   sourceStatuses ERROR、total=null 计数未知（不填 0）；撤权对象只显示
 *   inaccessibleCount 计数，不显示名称/状态/证据；
 * - investigation claim/handoff 直连原业务 API：409 携 currentRevision
 *   刷新后重试一次，仍冲突则如实中止并刷新清单；
 * - 入口经 Slice F 冻结构造点 wb360DrillQuery 跳 wb360 single（携
 *   loopId+证据/workRef），from=fullPath 供返回恢复清单位置；
 * - 最近继续：打开回路时经 PUT /me/work-resume 记录（服务端验证对象后
 *   才写入，失败打开不制造条目）；删除/清空携带 generation 栅栏。
 */

import type { PersonalWorkApi } from '#/api/personal-work';
import type { WorkbenchContext } from '#/views/loop/workbench360/workbench-context';

import { computed, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useUserStore } from '@vben/stores';

import {
  claimInvestigationApi,
  clearWorkResumeApi,
  deleteWorkResumeApi,
  extractRevisionConflict,
  getInvestigationApi,
  getMyWorkApi,
  getWorkResumeApi,
  handoffInvestigationApi,
  newRequestId,
  putWorkResumeApi,
  setLoopFollowApi,
} from '#/api/personal-work';
import { wb360DrillQuery } from '#/views/cockpit/wb-comps/use-drill';

type WorkItem = PersonalWorkApi.WorkItem;
type WorkKind = PersonalWorkApi.WorkKind;
type WorkSourceStatus = PersonalWorkApi.WorkSourceStatus;

/* ── 展示语义映射（纯函数，单测直接覆盖） ── */

/** 统一来源名 → 中文名（sourceStatuses 键与后端 personal_work 一致） */
export const SOURCE_LABEL: Record<string, string> = {
  action_item: '系统建议',
  follow: '我的关注',
  handling_order: '处置工单',
  investigation: '核查',
  resume: '最近继续',
};

/** 各视图参与的来源（与后端 _WORK_KIND_SOURCES 一致） */
export const VIEW_SOURCES: Record<WorkKind, string[]> = {
  ASSIGNED: ['investigation', 'handling_order'],
  FOLLOW: ['follow'],
  RESUME: ['resume'],
  SYSTEM_SUGGESTION: ['action_item'],
};

export const VIEW_LABEL: Record<WorkKind, string> = {
  ASSIGNED: '分配给我',
  FOLLOW: '我的关注',
  RESUME: '最近继续',
  SYSTEM_SUGGESTION: '系统建议',
};

/** 三分区展示元（互斥；等待/今日/可行动之外仅经筛选查询） */
export const PARTITION_META: Record<
  'ACTIONABLE' | 'TODAY' | 'WAITING',
  { desc: string; label: string }
> = {
  ACTIONABLE: { desc: '需要我现在继续处理的事项', label: '现在可行动' },
  TODAY: { desc: '计划在今日处理的事项（仍可操作）', label: '今日计划' },
  WAITING: { desc: '等数据、工况、审核或观察期', label: '等待条件' },
};

/** 分区筛选（默认=进行中的工作；未来/已结束仅经显式筛选查询） */
export type PartitionFilter = 'CLOSED' | 'DEFAULT' | 'FUTURE';

export const PARTITION_FILTER_LABEL: Record<PartitionFilter, string> = {
  CLOSED: '已结束',
  DEFAULT: '进行中（默认）',
  FUTURE: '未来计划',
};

/** 优先级理由 → 可解释标签（09§4.3：不生成新评分） */
const PRIORITY_REASON_LABEL: Record<string, string> = {
  HIGH_PRIORITY: '高优先级',
  OVERDUE: '逾期',
  REOPENED: '重开',
  SLA_BREACH: 'SLA 超期',
  SLA_WARN: 'SLA 预警',
};

export function priorityReasonLabel(reason: string): string {
  return PRIORITY_REASON_LABEL[reason] ?? reason;
}

/** 阶段标签（来源实体自带阶段，不复制状态机） */
const STAGE_LABEL: Record<string, string> = {
  CLOSED: '已结束',
  EXECUTING: '执行中',
  IN_PROGRESS: '核查中',
  PENDING: '待处理',
  REOPENED: '已重开',
  TODO: '待认领',
  VERIFYING: '验证中',
  WAITING: '等待中',
};

export function stageLabel(stage: null | string | undefined): string {
  if (!stage) return '—';
  return STAGE_LABEL[stage] ?? stage;
}

/** 撤权不可访问计数（当前视图来源合计；只显示计数不泄密） */
export function inaccessibleTotal(
  sourceStatuses: Record<string, WorkSourceStatus>,
  sources: string[],
): number {
  return sources.reduce(
    (n, s) => n + (sourceStatuses[s]?.inaccessibleCount ?? 0),
    0,
  );
}

/** 当前视图内失败的来源名（显式 ERROR，不填 0） */
export function failedSources(
  sourceStatuses: Record<string, WorkSourceStatus>,
  sources: string[],
): string[] {
  return sources.filter((s) => sourceStatuses[s]?.status === 'ERROR');
}

/**
 * 分配给我三分区分组（服务端已互斥判定；默认视图含
 * WAITING/TODAY/ACTIONABLE，FUTURE/CLOSED 经筛选查询为平铺列表）。
 */
export function groupAssignedByPartition(
  items: WorkItem[],
): Record<'ACTIONABLE' | 'TODAY' | 'WAITING', WorkItem[]> {
  return {
    ACTIONABLE: items.filter((i) => i.partition === 'ACTIONABLE'),
    TODAY: items.filter((i) => i.partition === 'TODAY'),
    WAITING: items.filter((i) => i.partition === 'WAITING'),
  };
}

/* ── 深链构造（Slice F 构造点；from=fullPath 返回恢复） ── */

/** 最近继续 context → 工作台上下文（按形态校验，非法项忽略不编造） */
export function parseResumeContext(
  context: null | Record<string, any> | undefined,
): Omit<WorkbenchContext, 'loopId'> {
  if (!context || typeof context !== 'object') return {};
  const out: Omit<WorkbenchContext, 'loopId'> = {};
  const section = context.section;
  if (
    typeof section === 'string' &&
    ['assess', 'diagnosis', 'handling', 'tuning'].includes(section)
  ) {
    out.section = section as WorkbenchContext['section'];
  }
  const win = context.window;
  if (
    win &&
    typeof win === 'object' &&
    typeof win.tsStart === 'string' &&
    typeof win.tsEnd === 'string' &&
    typeof win.timezone === 'string'
  ) {
    out.window = { tsStart: win.tsStart, tsEnd: win.tsEnd, timezone: win.timezone };
  }
  const refs = context.evidenceRefs;
  if (Array.isArray(refs)) {
    const parsed = refs
      .filter(
        (r: any) =>
          r && typeof r.kind === 'string' && typeof r.id === 'string',
      )
      .map((r: any) => ({
        id: r.id as string,
        kind: r.kind as string,
        ...(typeof r.version === 'string' ? { version: r.version as string } : {}),
      }));
    if (parsed.length > 0) out.evidenceRefs = parsed;
  }
  return out;
}

/**
 * 工作项 → wb360 single 深链（构造点 wb360DrillQuery）。
 * - 核查/工单/建议 → workRef 携带业务身份；工单/建议默认处置剖面；
 * - 关注 → 仅 loopId；最近继续 → 还原存储的上下文（剖面/窗口/证据）；
 * - from 由调用方传 route.fullPath（返回恢复清单位置/筛选/分页）。
 */
export function buildWorkbenchTarget(
  item: WorkItem,
  from: string,
): { path: string; query: Record<string, string> } {
  const context: WorkbenchContext = { loopId: item.loopId };
  if (item.workKind === 'RESUME') {
    Object.assign(context, parseResumeContext(item.context));
  } else if (item.workKind === 'FOLLOW') {
    // 关注不产生责任：仅打开回路，不携带工作身份
  } else {
    switch (item.sourceKind) {
    case 'action_item': {
      context.workRef = { id: item.workId, kind: 'action_item' };
      context.section = 'handling';
    
    break;
    }
    case 'handling_order': {
      context.workRef = { id: item.workId, kind: 'handling_order' };
      context.section = 'handling';
    
    break;
    }
    case 'investigation': {
      context.workRef = { id: item.workId, kind: 'investigation' };
    
    break;
    }
    // No default
    }
    if (item.evidenceRefs.length > 0) {
      context.evidenceRefs = item.evidenceRefs.map((e) => ({
        id: e.id,
        kind: e.kind,
        ...(e.version ? { version: e.version } : {}),
      }));
    }
  }
  return wb360DrillQuery(context, { from, mode: 'single' });
}

/* ── 操作权限（观察角色不出现可执行入口） ── */

const OPERATE_ROLES = new Set([
  'ADMIN',
  'EXPERT',
  'IC_ENGINEER',
  'PE_ENGINEER',
]);

function safeUser(): { roles: string[]; userId: null | string } {
  try {
    const userStore = useUserStore();
    return {
      roles: [...(userStore.userInfo?.roles ?? [])],
      userId: (userStore.userInfo?.userId as string | undefined) ?? null,
    };
  } catch {
    return { roles: [], userId: null };
  }
}

/* ── 动作结果（UI 如实提示，不编造成功） ── */

export interface ActionOutcome {
  /** 冲突被中止（他人已操作/状态已变）：附当前阶段/负责人供显示 */
  conflict?: {
    ownerDisplayName?: null | string;
    ownerId?: null | string;
    stage?: null | string;
  };
  ok: boolean;
}

/** claim：409 → 刷新详情 → 仍待认领且未被他人认领才以新版本重试一次 */
export async function claimInvestigationWithRetry(
  item: WorkItem,
): Promise<ActionOutcome> {
  const me = safeUser().userId;
  const attempt = (revision: number) =>
    claimInvestigationApi(item.workId, {
      expectedRevision: Math.max(1, revision),
      requestId: newRequestId(),
    });
  try {
    await attempt(item.revision ?? 1);
    return { ok: true };
  } catch (error) {
    if (!extractRevisionConflict(error)) return { ok: false };
    let fresh: PersonalWorkApi.InvestigationView;
    try {
      fresh = await getInvestigationApi(item.workId);
    } catch {
      return { ok: false };
    }
    const claimable =
      fresh.stage === 'TODO' && (!fresh.ownerId || fresh.ownerId === me);
    if (!claimable) {
      return {
        conflict: {
          ownerDisplayName: fresh.ownerDisplayName,
          ownerId: fresh.ownerId,
          stage: fresh.stage,
        },
        ok: false,
      };
    }
    try {
      await attempt(fresh.revision);
      return { ok: true };
    } catch (retryError) {
      const c2 = extractRevisionConflict(retryError);
      return {
        conflict: { stage: c2?.stage ?? null, ownerId: c2?.ownerId ?? null },
        ok: false,
      };
    }
  }
}

/** handoff：409 → 刷新详情 → 仍 IN_PROGRESS 且负责人是本人/ADMIN 才重试一次 */
export async function handoffInvestigationWithRetry(
  item: WorkItem,
  newOwnerId: string,
  note: null | string,
): Promise<ActionOutcome> {
  const me = safeUser().userId;
  const attempt = (revision: number) =>
    handoffInvestigationApi(item.workId, {
      expectedRevision: Math.max(1, revision),
      newOwnerId,
      note,
      requestId: newRequestId(),
    });
  try {
    await attempt(item.revision ?? 1);
    return { ok: true };
  } catch (error) {
    if (!extractRevisionConflict(error)) return { ok: false };
    let fresh: PersonalWorkApi.InvestigationView;
    try {
      fresh = await getInvestigationApi(item.workId);
    } catch {
      return { ok: false };
    }
    const operable = fresh.stage === 'IN_PROGRESS' && fresh.ownerId === me;
    if (!operable) {
      return {
        conflict: {
          ownerDisplayName: fresh.ownerDisplayName,
          ownerId: fresh.ownerId,
          stage: fresh.stage,
        },
        ok: false,
      };
    }
    try {
      await attempt(fresh.revision);
      return { ok: true };
    } catch (retryError) {
      const c2 = extractRevisionConflict(retryError);
      return {
        conflict: { stage: c2?.stage ?? null, ownerId: c2?.ownerId ?? null },
        ok: false,
      };
    }
  }
}

/* ── 页面数据层 ── */

export function useMyWork() {
  const router = useRouter();

  const activeView = ref<WorkKind>('ASSIGNED');
  const partitionFilter = ref<PartitionFilter>('DEFAULT');
  const items = ref<WorkItem[]>([]);
  /** null = 计数未知（部分来源失败）；绝不显示伪造 0 */
  const total = ref<null | number>(null);
  const readStatus = ref<PersonalWorkApi.ReadStatus>('COMPLETE');
  const readAt = ref('');
  const sourceStatuses = ref<Record<string, WorkSourceStatus>>({});
  const nextCursor = ref<null | string>(null);
  const loading = ref(false);
  const loadingMore = ref(false);
  /** 整请求失败（网络/5xx）——与来源级 ERROR 区分显示 */
  const loadError = ref('');
  /** 动作结果提示（成功/冲突/失败如实文案） */
  const notice = reactive({ text: '', type: 'info' as 'error' | 'info' | 'success' | 'warning' });
  const actionBusy = reactive<Record<string, boolean>>({});

  /** 最近继续代际/版本镜像（RESUME 视图激活时加载；删除/清空/记录用） */
  const resumeGeneration = ref<null | number>(null);
  const resumeRevisions = new Map<string, number>();

  const viewSources = computed(() => VIEW_SOURCES[activeView.value]);
  const viewInaccessible = computed(() =>
    inaccessibleTotal(sourceStatuses.value, viewSources.value),
  );
  const viewFailedSources = computed(() =>
    failedSources(sourceStatuses.value, viewSources.value),
  );

  /** 分配给我三分区（仅默认筛选下有分区语义） */
  const assignedGroups = computed(() =>
    activeView.value === 'ASSIGNED' && partitionFilter.value === 'DEFAULT'
      ? groupAssignedByPartition(items.value)
      : null,
  );

  function applyData(
    data: PersonalWorkApi.WorkData,
    { append }: { append: boolean },
  ) {
    items.value = append ? [...items.value, ...data.items] : data.items;
    total.value = data.total ?? null;
    readStatus.value = data.readStatus;
    readAt.value = data.readAt;
    sourceStatuses.value = data.sourceStatuses ?? {};
    nextCursor.value = data.nextCursor ?? null;
  }

  async function load(options?: { append: boolean }) {
    const append = options?.append ?? false;
    if (append) loadingMore.value = true;
    else loading.value = true;
    if (!append) loadError.value = '';
    try {
      const query: PersonalWorkApi.WorkQuery = {
        pageSize: 50,
        workKind: activeView.value,
      };
      if (activeView.value === 'ASSIGNED' && partitionFilter.value !== 'DEFAULT') {
        query.partition = partitionFilter.value;
      }
      if (append && nextCursor.value) query.cursor = nextCursor.value;
      const data = await getMyWorkApi(query);
      applyData(data, { append });
    } catch (error) {
      if (!append) {
        items.value = [];
        total.value = null;
        sourceStatuses.value = {};
        nextCursor.value = null;
        readStatus.value = 'ERROR';
        loadError.value =
          error instanceof Error && error.message
            ? `工作清单加载失败（${error.message}）`
            : '工作清单加载失败，请重试';
      }
    } finally {
      loading.value = false;
      loadingMore.value = false;
    }
  }

  async function loadMore() {
    if (!nextCursor.value || loadingMore.value) return;
    await load({ append: true });
  }

  /** 最近继续代际/版本镜像（删除/清空/记录必须携带当前 generation） */
  async function refreshResumeState(): Promise<boolean> {
    try {
      const data = await getWorkResumeApi({ kind: 'loop', limit: 100 });
      resumeGeneration.value = data.currentGeneration;
      resumeRevisions.clear();
      for (const it of data.items) {
        resumeRevisions.set(`${it.resourceKind}:${it.resourceId}`, it.revision);
      }
      return true;
    } catch {
      return false;
    }
  }

  async function switchView(kind: WorkKind) {
    if (activeView.value === kind) return;
    activeView.value = kind;
    partitionFilter.value = 'DEFAULT';
    notice.text = '';
    if (kind === 'RESUME') await refreshResumeState();
    await load();
  }

  async function setPartitionFilter(filter: PartitionFilter) {
    if (partitionFilter.value === filter) return;
    partitionFilter.value = filter;
    await load();
  }

  async function reload() {
    if (activeView.value === 'RESUME') await refreshResumeState();
    await load();
  }

  /** 认领核查（409 刷新重试；结果如实提示并刷新清单） */
  async function claim(item: WorkItem) {
    actionBusy[item.workId] = true;
    try {
      const outcome = await claimInvestigationWithRetry(item);
      if (outcome.ok) {
        notice.type = 'success';
        notice.text = '已认领核查';
      } else if (outcome.conflict) {
        notice.type = 'warning';
        const owner = outcome.conflict.ownerDisplayName;
        notice.text = owner
          ? `认领未成功：该核查已由 ${owner} 负责或状态已变化，清单已刷新`
          : '认领未成功：核查状态已变化（可能已被他人认领），清单已刷新';
      } else {
        notice.type = 'error';
        notice.text = '认领失败，请重试';
      }
    } finally {
      Reflect.deleteProperty(actionBusy, item.workId);
      await reload();
    }
  }

  /** 移交核查（接手人只接受稳定 sys_user.id；409 刷新重试） */
  async function handoff(item: WorkItem, newOwnerId: string, note: null | string) {
    actionBusy[item.workId] = true;
    try {
      const outcome = await handoffInvestigationWithRetry(item, newOwnerId, note);
      if (outcome.ok) {
        notice.type = 'success';
        notice.text = '已移交核查';
      } else if (outcome.conflict) {
        notice.type = 'warning';
        notice.text = '移交未成功：核查状态已变化，清单已刷新';
      } else {
        notice.type = 'error';
        notice.text = '移交失败，请重试';
      }
    } finally {
      Reflect.deleteProperty(actionBusy, item.workId);
      await reload();
    }
  }

  /** 取消关注（设定语义；取消不产生任何责任/删除动作；409 刷新重试一次） */
  async function unfollow(item: WorkItem): Promise<void> {
    actionBusy[item.workId] = true;
    try {
      const request = (revision: number) =>
        setLoopFollowApi(item.loopId, {
          expectedRevision: revision,
          isFollowed: false,
          requestId: newRequestId(),
        });
      try {
        await request(item.revision ?? 0);
        notice.type = 'success';
        notice.text = '已取消关注（不影响已分派工作与回路本身）';
      } catch (error) {
        const conflict = extractRevisionConflict(error);
        if (!conflict) {
          notice.type = 'error';
          notice.text = '取消关注失败，请重试';
          return;
        }
        if (conflict.isFollowed === false) {
          // 已是取消状态（他处已操作/幂等收敛）：按成功口径刷新
          notice.type = 'success';
          notice.text = '已取消关注（不影响已分派工作与回路本身）';
          return;
        }
        try {
          await request(conflict.currentRevision ?? item.revision ?? 0);
          notice.type = 'success';
          notice.text = '已取消关注（不影响已分派工作与回路本身）';
        } catch {
          notice.type = 'warning';
          notice.text = '关注状态已变化，清单已刷新';
        }
      }
    } finally {
      Reflect.deleteProperty(actionBusy, item.workId);
      await reload();
    }
  }

  /** 删除单条最近继续（generation 栅栏；409 刷新代际重试一次） */
  async function removeResume(item: WorkItem): Promise<void> {
    actionBusy[item.workId] = true;
    try {
      if (resumeGeneration.value === null) await refreshResumeState();
      const gen = resumeGeneration.value;
      if (gen === null) {
        notice.type = 'error';
        notice.text = '最近继续状态读取失败，请重试';
        return;
      }
      try {
        await deleteWorkResumeApi('loop', item.workId, gen);
        notice.type = 'success';
        notice.text = '已删除该条最近继续';
      } catch (error) {
        const conflict = extractRevisionConflict(error);
        if (!conflict) {
          notice.type = 'error';
          notice.text = '删除失败，请重试';
          return;
        }
        await refreshResumeState();
        const gen2 = resumeGeneration.value;
        if (gen2 === null) {
          notice.type = 'error';
          notice.text = '最近继续状态读取失败，请重试';
          return;
        }
        try {
          await deleteWorkResumeApi('loop', item.workId, gen2);
          notice.type = 'success';
          notice.text = '已删除该条最近继续';
        } catch {
          notice.type = 'warning';
          notice.text = '最近继续已变化，清单已刷新';
        }
      }
    } finally {
      Reflect.deleteProperty(actionBusy, item.workId);
      await reload();
    }
  }

  /** 清空最近继续（服务端先原子增代际再清旧代；旧代迟到请求不得复活） */
  async function clearResumes(): Promise<void> {
    actionBusy.__clearAll = true;
    try {
      await clearWorkResumeApi();
      notice.type = 'success';
      notice.text = '已清空最近继续';
    } catch {
      notice.type = 'error';
      notice.text = '清空失败，请重试';
    } finally {
      Reflect.deleteProperty(actionBusy, '__clearAll');
      await refreshResumeState();
      await load();
    }
  }

  /**
   * 打开工作项 → wb360 single（构造点深链 + from 返回恢复）。
   * 打开回路同时记录最近继续（fire-and-forget：服务端验证对象后才写入；
   * 失败不阻塞导航、不显示已保存）。
   */
  async function openItem(item: WorkItem, from: string) {
    const target = buildWorkbenchTarget(item, from);
    recordLoopResumeQuietly(item);
    await router.push(target);
  }

  /** 记录最近继续（generation+expectedRevision 协议；409 刷新代际重试一次后放弃） */
  async function recordLoopResumeQuietly(item: WorkItem) {
    try {
      if (resumeGeneration.value === null) await refreshResumeState();
      const context: Record<string, any> = {};
      const parsed = parseResumeContext(item.context);
      if (parsed.section) context.section = parsed.section;
      if (parsed.window) context.window = parsed.window;
      const refs =
        item.workKind === 'RESUME' ? parsed.evidenceRefs : undefined;
      const evidence = refs ??
        (item.evidenceRefs.length > 0
          ? item.evidenceRefs.map((e) => ({
              id: e.id,
              kind: e.kind,
              ...(e.version ? { version: e.version } : {}),
            }))
          : undefined);
      if (evidence && evidence.length > 0) context.evidenceRefs = evidence;
      const section =
        item.sourceKind === 'handling_order' || item.sourceKind === 'action_item'
          ? 'handling'
          : undefined;
      if (!context.section && section) context.section = section;

      const key = `loop:${item.loopId}`;
      const attempt = async () => {
        const gen = resumeGeneration.value;
        if (gen === null) return false;
        await putWorkResumeApi('loop', item.loopId, {
          context,
          expectedRevision: resumeRevisions.get(key) ?? 0,
          generation: gen,
          requestId: newRequestId(),
        });
        return true;
      };
      try {
        await attempt();
      } catch (error) {
        if (!extractRevisionConflict(error)) return;
        await refreshResumeState();
        try {
          await attempt();
        } catch {
          /* 记录最近继续失败不阻塞导航、不提示已保存 */
        }
      }
    } catch {
      /* 同上：静默失败（无已保存假象） */
    }
  }

  return {
    actionBusy,
    activeView,
    assignedGroups,
    claim,
    clearResumes,
    handoff,
    items,
    load,
    loadError,
    loading,
    loadingMore,
    loadMore,
    nextCursor,
    notice,
    openItem,
    partitionFilter,
    readAt,
    readStatus,
    reload,
    removeResume,
    resumeGeneration,
    setPartitionFilter,
    sourceStatuses,
    switchView,
    total,
    unfollow,
    viewFailedSources,
    viewInaccessible,
  };
}

/** 当前用户是否可执行业务操作（观察角色 SPONSOR 不出现认领/移交/处置入口） */
export function canOperateOnWork(roles: readonly string[]): boolean {
  return roles.some((r) => OPERATE_ROLES.has(r));
}

export { safeUser };
