/**
 * P6-06（O06-NAV）我的工作页面与数据层测试
 *
 * 覆盖任务书验证口径：
 * - 四视图聚合渲染（分配给我/系统建议/我的关注/最近继续）；
 * - 三分区互斥（等待/今日计划/现在可行动，单项仅归其一）；
 * - investigation claim 409 刷新重试流（仍待认领→新版本重试；已被认领→
 *   如实中止不冒充成功）；handoff 同理（负责人已变更→中止）；
 * - 撤权计数不泄密（inaccessibleCount 只显示计数，清单不含越权对象）；
 * - 来源失败如实 ERROR 显式（sourceStatuses ERROR、total=null 计数未知
 *   不填 0，健康来源数据仍可见）；
 * - 入口深链 query 完整性（loopId+证据/workRef+from，经
 *   resolveWb360Route 往返还原）；
 * - 断网未保存态（页面徽标随 saveStates 如实显示）。
 */
import type { PersonalWorkApi } from '#/api/personal-work';

import { flushPromises, mount } from '@vue/test-utils';

import { useAccessStore, useUserStore } from '@vben/stores';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

type WorkData = PersonalWorkApi.WorkData;
type WorkItem = PersonalWorkApi.WorkItem;

const api = vi.hoisted(() => ({
  claimInvestigationApi: vi.fn(),
  clearWorkResumeApi: vi.fn(),
  deleteWorkResumeApi: vi.fn(),
  getInvestigationApi: vi.fn(),
  getMyWorkApi: vi.fn(),
  getWorkResumeApi: vi.fn(),
  getWorkspacePreferencesApi: vi.fn(),
  handoffInvestigationApi: vi.fn(),
  putWorkResumeApi: vi.fn(),
  putWorkspacePreferenceApi: vi.fn(),
  setLoopFollowApi: vi.fn(),
}));

vi.mock('#/api/personal-work', async () => {
  const actual = await vi.importActual<any>('#/api/personal-work');
  return {
    ...actual,
    claimInvestigationApi: (...a: any[]) => api.claimInvestigationApi(...a),
    clearWorkResumeApi: (...a: any[]) => api.clearWorkResumeApi(...a),
    deleteWorkResumeApi: (...a: any[]) => api.deleteWorkResumeApi(...a),
    getInvestigationApi: (...a: any[]) => api.getInvestigationApi(...a),
    getMyWorkApi: (...a: any[]) => api.getMyWorkApi(...a),
    getWorkResumeApi: (...a: any[]) => api.getWorkResumeApi(...a),
    getWorkspacePreferencesApi: (...a: any[]) =>
      api.getWorkspacePreferencesApi(...a),
    handoffInvestigationApi: (...a: any[]) => api.handoffInvestigationApi(...a),
    putWorkResumeApi: (...a: any[]) => api.putWorkResumeApi(...a),
    putWorkspacePreferenceApi: (...a: any[]) =>
      api.putWorkspacePreferenceApi(...a),
    setLoopFollowApi: (...a: any[]) => api.setLoopFollowApi(...a),
  };
});

/* use-drill 构造点依赖的 API 模块（本组测试不触发真实请求） */
vi.mock('#/api/metric', () => ({
  getLoopConfidenceLatestApi: vi.fn(),
  getLoopSnapshotsApi: vi.fn(),
  getNodeRankingApi: vi.fn(),
  getRankingApi: vi.fn(),
}));
vi.mock('#/api/alert', () => ({ getAlertEventsApi: vi.fn() }));
vi.mock('#/api/diagnosis', () => ({ getDiagnosisRunsApi: vi.fn() }));
vi.mock('#/api/handling', () => ({
  getHandlingOrdersApi: vi.fn(),
  getHandlingSuggestionsApi: vi.fn(),
}));
vi.mock('#/api/tuning', () => ({ getTuningTasksApi: vi.fn() }));

const routerPush = vi.hoisted(() => vi.fn());
vi.mock('vue-router', () => ({
  useRoute: () => ({ fullPath: '/my-work', query: {} }),
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div class="page-stub"><slot /></div>' },
}));

vi.mock('ant-design-vue', async () => {
  const actual = await vi.importActual<any>('ant-design-vue');
  const stub = (tpl: string) => ({ template: tpl });
  return {
    ...actual,
    Alert: {
      props: ['message', 'type'],
      template:
        '<div class="alert-stub" :data-type="type"><slot name="message">{{ message }}</slot><slot name="action" /></div>',
    },
    Button: {
      props: ['type', 'size', 'loading', 'disabled'],
      template:
        '<button class="btn-stub" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
    },
    Card: stub('<div class="card-stub"><slot /></div>'),
    Input: {
      props: ['value'],
      template: '<input class="input-stub" />',
    },
    Modal: { ...stub('<div class="modal-stub"><slot /></div>'), confirm: vi.fn() },
    Spin: stub('<div class="spin-stub"><slot /></div>'),
    TabPane: stub('<div class="tabpane-stub" />'),
    Tabs: {
      props: ['activeKey'],
      template:
        '<div class="tabs-stub">' +
        '<button v-for="k in [\'ASSIGNED\',\'SYSTEM_SUGGESTION\',\'FOLLOW\',\'RESUME\']"' +
        ' :key="k" :data-tab="k" @click="$emit(\'change\', k)">{{ k }}</button>' +
        '<slot /></div>',
    },
    Tag: stub('<span class="tag-stub"><slot /></span>'),
    Textarea: stub('<textarea class="textarea-stub" />'),
    Tooltip: stub('<span class="tooltip-stub"><slot /></span>'),
    message: { error: vi.fn(), success: vi.fn() },
  };
});

import {
  resetClpmPreferenceState,
  setDefaultHomePreference,
} from '#/composables/use-clpm-preferences';
import { resolveWb360Route } from '#/views/loop/workbench360/workbench-context';
import MyWorkPage from '#/views/my-work/index.vue';
import {
  buildWorkbenchTarget,
  canOperateOnWork,
  claimInvestigationWithRetry,
  groupAssignedByPartition,
  handoffInvestigationWithRetry,
  inaccessibleTotal,
  parseResumeContext,
  useMyWork,
} from '#/views/my-work/use-my-work';

/* ── fixtures ── */

function workItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    evidenceRefs: [],
    loopId: 'loop-1',
    loopTagName: 'TIC-101',
    nextAction: { kind: 'open_investigation', label: '继续核查' },
    partition: 'ACTIONABLE',
    priorityReasons: [],
    reason: '诊断来源的核查，我负责',
    responsibilityPending: false,
    revision: 2,
    sourceKind: 'investigation',
    stage: 'IN_PROGRESS',
    title: 'TIC-101 振荡疑似',
    unitName: '常减压装置',
    updatedAt: '2026-10-13T08:00:00Z',
    workId: 'inv-1',
    workKind: 'ASSIGNED',
    ...overrides,
  };
}

function workData(overrides: Partial<WorkData> = {}): WorkData {
  return {
    items: [],
    nextCursor: null,
    readAt: '2026-10-14T01:00:00Z',
    readStatus: 'COMPLETE',
    sourceStatuses: {},
    total: 0,
    ...overrides,
  };
}

const ASSIGNED_ITEMS = [
  workItem({
    nextAction: { kind: 'review_wait_condition', label: '复查等待条件' },
    partition: 'WAITING',
    stage: 'WAITING',
    title: '等待工况恢复的核查',
    waitReason: '等待装置负荷恢复',
    waitReviewCondition: '负荷 > 70% 持续 2 小时',
    workId: 'inv-wait',
  }),
  workItem({
    partition: 'TODAY',
    plannedAt: '2026-10-14T02:00:00Z',
    sourceKind: 'handling_order',
    stage: 'EXECUTING',
    title: '今日计划执行的工单',
    workId: 'order-today',
  }),
  workItem({
    partition: 'ACTIONABLE',
    priorityReasons: ['OVERDUE'],
    title: '可行动核查 A',
    workId: 'inv-a',
  }),
];

function defaultAssignedData(): WorkData {
  return workData({
    items: ASSIGNED_ITEMS,
    sourceStatuses: {
      handling_order: { inaccessibleCount: 0, status: 'OK', total: 1 },
      investigation: { inaccessibleCount: 0, status: 'OK', total: 2 },
    },
    total: 3,
  });
}

function setAccount(
  userId = 'user-1',
  roles = ['ADMIN'],
  codes: string[] = [],
) {
  const userStore = useUserStore();
  const accessStore = useAccessStore();
  userStore.setUserInfo({ realName: 'T', roles, userId, username: 't' } as any);
  accessStore.setAccessCodes(codes);
}

function conflict409(data: Record<string, any>) {
  return {
    response: {
      data: {
        code: 'ERR_REVISION_CONFLICT',
        data,
        message: '核查版本冲突，请刷新后重试',
      },
      status: 409,
    },
  };
}

function mountPage() {
  return mount(MyWorkPage);
}

async function clickTab(wrapper: any, tab: string) {
  await wrapper.find(`[data-tab="${tab}"]`).trigger('click');
  await flushPromises();
}

describe('我的工作（P6-06 O06-NAV）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    setAccount();
    resetClpmPreferenceState(); // 模块级偏好状态（含 serverSyncFailed）跨用例隔离
    for (const fn of Object.values(api)) fn.mockReset();
    routerPush.mockReset();
    api.getMyWorkApi.mockImplementation((q: any) =>
      Promise.resolve(q?.workKind === 'ASSIGNED' || !q?.workKind
        ? defaultAssignedData()
        : workData()),
    );
    api.getWorkResumeApi.mockResolvedValue({
      currentGeneration: 3,
      inaccessibleCount: 0,
      items: [],
      readAt: '2026-10-14T01:00:00Z',
      readStatus: 'COMPLETE',
    });
    api.getWorkspacePreferencesApi.mockResolvedValue({
      items: {},
      readAt: '2026-10-14T01:00:00Z',
    });
    api.putWorkResumeApi.mockResolvedValue({
      generation: 3,
      lastVisitedAt: '2026-10-14T01:00:00Z',
      replayed: false,
      resourceId: 'loop-1',
      resourceKind: 'loop',
      revision: 1,
    });
  });

  describe('四视图聚合渲染', () => {
    it('分配给我：默认视图三分区呈现；切换系统建议/我的关注/最近继续按 workKind 查询渲染', async () => {
      const followData = workData({
        items: [
          workItem({
            nextAction: { kind: 'open_loop_workbench', label: '打开回路工作台' },
            partition: null,
            reason: '我关注的回路（关注不产生责任，不自动变成待办）',
            sourceKind: 'follow',
            stage: null,
            title: 'TIC-201',
            workId: 'loop-2',
            workKind: 'FOLLOW',
          }),
        ],
        sourceStatuses: {
          follow: { inaccessibleCount: 0, status: 'OK', total: 1 },
        },
        total: 1,
      });
      const suggestionData = workData({
        items: [
          workItem({
            nextAction: { kind: 'review_action_item', label: '审核建议' },
            reason: '系统按诊断结论生成的处置建议，待审核',
            sourceKind: 'action_item',
            stage: 'PENDING',
            title: '建议：检查 TIC-301 阀门',
            workId: 'ai-1',
            workKind: 'SYSTEM_SUGGESTION',
          }),
        ],
        sourceStatuses: {
          action_item: { inaccessibleCount: 0, status: 'OK', total: 1 },
        },
        total: 1,
      });
      const resumeData = workData({
        items: [
          workItem({
            context: { section: 'tuning' },
            nextAction: { kind: 'resume_context', label: '回到上次位置' },
            partition: null,
            reason: '最近访问的回路，从原位置继续',
            sourceKind: 'resume',
            stage: null,
            title: 'TIC-401',
            workId: 'loop-4',
            workKind: 'RESUME',
          }),
        ],
        sourceStatuses: {
          resume: { inaccessibleCount: 0, status: 'OK', total: 1 },
        },
        total: 1,
      });
      api.getMyWorkApi.mockImplementation((q: any) => {
        if (q?.workKind === 'FOLLOW') return Promise.resolve(followData);
        if (q?.workKind === 'SYSTEM_SUGGESTION')
          return Promise.resolve(suggestionData);
        if (q?.workKind === 'RESUME') return Promise.resolve(resumeData);
        return Promise.resolve(defaultAssignedData());
      });

      const w = mountPage();
      await flushPromises();

      // 分配给我（默认）：三分区 + 计数
      expect(w.find('[data-view="ASSIGNED"]').exists()).toBe(true);
      expect(
        w.find('[data-partition-section="ACTIONABLE"]').text(),
      ).toContain('可行动核查 A');
      expect(w.find('[data-partition-section="TODAY"]').text()).toContain(
        '今日计划执行的工单',
      );
      expect(w.find('[data-partition-section="WAITING"]').text()).toContain(
        '等待工况恢复的核查',
      );
      expect(w.find('[data-testid="work-total"]').text()).toBe('共 3 条');

      // 系统建议
      await clickTab(w, 'SYSTEM_SUGGESTION');
      expect(api.getMyWorkApi).toHaveBeenLastCalledWith(
        expect.objectContaining({ workKind: 'SYSTEM_SUGGESTION' }),
      );
      expect(w.text()).toContain('建议：检查 TIC-301 阀门');

      // 我的关注（含取消关注按钮——关注不产生责任）
      await clickTab(w, 'FOLLOW');
      expect(w.text()).toContain('TIC-201');
      expect(w.text()).toContain('取消关注');

      // 最近继续（激活时加载代际状态；含删除/清空入口）
      await clickTab(w, 'RESUME');
      expect(api.getWorkResumeApi).toHaveBeenCalled();
      expect(w.text()).toContain('TIC-401');
      expect(w.text()).toContain('删除记录');
      expect(w.text()).toContain('清空最近继续');
    });
  });

  describe('分区互斥', () => {
    it('单项仅归入一个分区；分组计数与明细一致', () => {
      const groups = groupAssignedByPartition(ASSIGNED_ITEMS);
      expect(groups.ACTIONABLE.map((i) => i.workId)).toEqual(['inv-a']);
      expect(groups.TODAY.map((i) => i.workId)).toEqual(['order-today']);
      expect(groups.WAITING.map((i) => i.workId)).toEqual(['inv-wait']);
      const union = [
        ...groups.ACTIONABLE,
        ...groups.TODAY,
        ...groups.WAITING,
      ].map((i) => i.workId);
      expect(new Set(union).size).toBe(ASSIGNED_ITEMS.length);
    });

    it('筛选 FUTURE/CLOSED 经 partition 参数显式查询（不进默认视图）', async () => {
      const w = mountPage();
      await flushPromises();
      const futureBtn = w
        .findAll('.btn-stub')
        .find((b: any) => b.text() === '未来计划');
      await futureBtn!.trigger('click');
      await flushPromises();
      expect(api.getMyWorkApi).toHaveBeenLastCalledWith(
        expect.objectContaining({ partition: 'FUTURE', workKind: 'ASSIGNED' }),
      );
    });
  });

  describe('claim/handoff 409 刷新重试流', () => {
    it('claim：409 → 刷新详情仍待认领 → 以新版本重试成功', async () => {
      api.claimInvestigationApi
        .mockRejectedValueOnce(
          conflict409({
            currentRevision: 5,
            investigationId: 'inv-1',
            ownerId: null,
            stage: 'TODO',
          }),
        )
        .mockResolvedValueOnce({ item: {}, replayed: false });
      api.getInvestigationApi.mockResolvedValueOnce({
        ownerId: null,
        revision: 5,
        stage: 'TODO',
      });
      const item = workItem({
        nextAction: { kind: 'claim_investigation', label: '认领核查' },
        stage: 'TODO',
      });
      const outcome = await claimInvestigationWithRetry(item);
      expect(outcome.ok).toBe(true);
      expect(api.claimInvestigationApi).toHaveBeenCalledTimes(2);
      // 重试携带刷新后的版本
      expect(api.claimInvestigationApi.mock.calls[1]![1]).toEqual(
        expect.objectContaining({ expectedRevision: 5 }),
      );
      // 两次请求身份不同（重试不是幂等重放）
      const [r1, r2] = api.claimInvestigationApi.mock.calls.map(
        (c) => c[1].requestId,
      );
      expect(r1).not.toBe(r2);
    });

    it('claim：409 → 刷新详情已被他人认领 → 如实中止，不重试不冒充成功', async () => {
      api.claimInvestigationApi.mockRejectedValueOnce(
        conflict409({
          currentRevision: 6,
          investigationId: 'inv-1',
          ownerId: 'user-9',
          stage: 'IN_PROGRESS',
        }),
      );
      api.getInvestigationApi.mockResolvedValueOnce({
        ownerDisplayName: '王工',
        ownerId: 'user-9',
        revision: 6,
        stage: 'IN_PROGRESS',
      });
      const item = workItem({
        nextAction: { kind: 'claim_investigation', label: '认领核查' },
        stage: 'TODO',
      });
      const outcome = await claimInvestigationWithRetry(item);
      expect(outcome.ok).toBe(false);
      expect(outcome.conflict?.ownerId).toBe('user-9');
      expect(api.claimInvestigationApi).toHaveBeenCalledTimes(1);
    });

    it('handoff：409 → 刷新详情负责人已变更 → 如实中止；仍本人负责 → 新版本重试', async () => {
      const item = workItem({ ownerId: 'user-1', stage: 'IN_PROGRESS' });
      // 负责人已变更 → 中止
      api.handoffInvestigationApi.mockRejectedValueOnce(
        conflict409({ currentRevision: 4, ownerId: 'user-8', stage: 'IN_PROGRESS' }),
      );
      api.getInvestigationApi.mockResolvedValueOnce({
        ownerId: 'user-8',
        revision: 4,
        stage: 'IN_PROGRESS',
      });
      const aborted = await handoffInvestigationWithRetry(item, 'user-2', null);
      expect(aborted.ok).toBe(false);
      expect(aborted.conflict?.ownerId).toBe('user-8');
      expect(api.handoffInvestigationApi).toHaveBeenCalledTimes(1);

      // 仍本人负责 → 重试成功
      api.handoffInvestigationApi.mockReset();
      api.handoffInvestigationApi
        .mockRejectedValueOnce(
          conflict409({ currentRevision: 7, ownerId: 'user-1', stage: 'IN_PROGRESS' }),
        )
        .mockResolvedValueOnce({ replayed: false, source: {}, target: {} });
      api.getInvestigationApi.mockReset();
      api.getInvestigationApi.mockResolvedValueOnce({
        ownerId: 'user-1',
        revision: 7,
        stage: 'IN_PROGRESS',
      });
      const retried = await handoffInvestigationWithRetry(item, 'user-2', '交接');
      expect(retried.ok).toBe(true);
      expect(api.handoffInvestigationApi).toHaveBeenCalledTimes(2);
      expect(api.handoffInvestigationApi.mock.calls[1]![1]).toEqual(
        expect.objectContaining({ expectedRevision: 7, newOwnerId: 'user-2' }),
      );
    });

    it('页面认领按钮：成功后刷新清单并提示', async () => {
      api.claimInvestigationApi.mockResolvedValueOnce({
        item: {},
        replayed: false,
      });
      const claimData = workData({
        items: [
          workItem({
            nextAction: { kind: 'claim_investigation', label: '认领核查' },
            partition: 'ACTIONABLE',
            stage: 'TODO',
            title: '待认领核查 X',
            workId: 'inv-claim',
          }),
        ],
        sourceStatuses: {
          investigation: { inaccessibleCount: 0, status: 'OK', total: 1 },
        },
        total: 1,
      });
      api.getMyWorkApi.mockResolvedValue(claimData);
      const w = mountPage();
      await flushPromises();
      const claimBtn = w
        .findAll('.btn-stub')
        .find((b: any) => b.text() === '认领核查');
      expect(claimBtn).toBeDefined();
      await claimBtn!.trigger('click');
      await flushPromises();
      expect(api.claimInvestigationApi).toHaveBeenCalledWith(
        'inv-claim',
        expect.objectContaining({ expectedRevision: 2 }),
      );
      expect(w.find('[data-testid="work-notice"]').text()).toContain('已认领核查');
    });
  });

  describe('撤权计数不泄密', () => {
    it('inaccessibleCount 只显示计数；清单不含越权对象名称', async () => {
      const followPartial = workData({
        items: [
          workItem({
            nextAction: { kind: 'open_loop_workbench', label: '打开回路工作台' },
            partition: null,
            sourceKind: 'follow',
            stage: null,
            title: 'TIC-201',
            workId: 'loop-2',
            workKind: 'FOLLOW',
          }),
        ],
        readStatus: 'PARTIAL',
        sourceStatuses: {
          follow: { inaccessibleCount: 2, status: 'OK', total: 1 },
        },
        total: 1,
      });
      api.getMyWorkApi.mockImplementation((q: any) =>
        Promise.resolve(q?.workKind === 'FOLLOW' ? followPartial : defaultAssignedData()),
      );
      const w = mountPage();
      await flushPromises();
      await clickTab(w, 'FOLLOW');
      const notice = w.find('[data-testid="inaccessible-count"]');
      expect(notice.exists()).toBe(true);
      expect(notice.text()).toContain('有 2 个对象当前不可访问');
      // 仅 1 个可访问对象渲染；不存在任何越权对象名称（计数之外零信息）
      expect(w.findAll('[data-work-kind="FOLLOW"]')).toHaveLength(1);
      expect(w.text()).toContain('TIC-201');
      // 计数纯函数
      expect(
        inaccessibleTotal(followPartial.sourceStatuses, ['follow']),
      ).toBe(2);
    });
  });

  describe('来源失败如实 ERROR 显式', () => {
    it('investigation 来源 ERROR：显式提示+计数未知不填 0，健康来源数据仍可见', async () => {
      const partial = workData({
        items: [ASSIGNED_ITEMS[1]!], // handling_order 健康来源条目
        readStatus: 'PARTIAL',
        sourceStatuses: {
          handling_order: { inaccessibleCount: 0, status: 'OK', total: 1 },
          investigation: { inaccessibleCount: 0, status: 'ERROR', total: null },
        },
        total: null,
      });
      api.getMyWorkApi.mockResolvedValue(partial);
      const w = mountPage();
      await flushPromises();
      const alert = w.find('[data-testid="work-read-partial"]');
      expect(alert.exists()).toBe(true);
      expect(alert.text()).toContain('核查');
      expect(alert.text()).toContain('暂不可用');
      // total=null → 计数未知（绝不显示伪造 0）
      expect(w.find('[data-testid="work-total"]').text()).toContain('计数未知');
      expect(w.find('[data-testid="work-total"]').text()).not.toContain('共 0 条');
      // 健康来源数据仍可见
      expect(w.text()).toContain('今日计划执行的工单');
    });

    it('整请求失败（断网/5xx）：显式失败态 + 重试，不显示空清单伪装无工作', async () => {
      api.getMyWorkApi.mockRejectedValue(new Error('ERR_NETWORK'));
      const w = mountPage();
      await flushPromises();
      expect(w.find('[data-testid="work-load-error"]').exists()).toBe(true);
      expect(w.find('[data-testid="work-load-error"]').text()).toContain(
        '加载失败',
      );
    });
  });

  describe('入口深链 query 完整性', () => {
    it('核查项：loopId+workRef+证据+from 全键构造，经 resolveWb360Route 往返还原', () => {
      const item = workItem({
        evidenceRefs: [{ id: 'run-9', kind: 'diagnosis_run', version: 'v2' }],
        workId: 'inv-77',
      });
      const { path, query } = buildWorkbenchTarget(item, '/my-work');
      expect(path).toBe('/loop/workbench360');
      expect(query).toEqual({
        from: '/my-work',
        loopId: 'loop-1',
        mode: 'single',
        recordId: 'run-9',
        recordKind: 'diagnosis_run',
        recordVersion: 'v2',
        workId: 'inv-77',
        workKind: 'investigation',
      });
      // 消费端往返：上下文与返回位置完整还原
      const r = resolveWb360Route(query);
      expect(r.errors).toEqual([]);
      expect(r.mode).toBe('single');
      expect(r.context?.loopId).toBe('loop-1');
      expect(r.context?.workRef).toEqual({ id: 'inv-77', kind: 'investigation' });
      expect(r.context?.evidenceRefs).toEqual([
        { id: 'run-9', kind: 'diagnosis_run', version: 'v2' },
      ]);
      expect(r.returnRef).toBe('/my-work');
    });

    it('工单项：默认处置剖面 + workRef=handling_order；最近继续：还原存储上下文（剖面/窗口/证据）', () => {
      const order = workItem({
        sourceKind: 'handling_order',
        workId: 'order-9',
      });
      const orderTarget = buildWorkbenchTarget(order, '/my-work');
      expect(orderTarget.query.section).toBe('handling');
      expect(orderTarget.query.workKind).toBe('handling_order');
      expect(orderTarget.query.workId).toBe('order-9');

      const resume = workItem({
        context: {
          evidenceRefs: [{ id: 'rec-1', kind: 'kpi_snapshot' }],
          section: 'tuning',
          window: {
            timezone: 'Asia/Shanghai',
            tsEnd: '2026-10-14T00:00:00.000Z',
            tsStart: '2026-10-13T00:00:00.000Z',
          },
        },
        workId: 'loop-1',
        workKind: 'RESUME',
      });
      const resumeTarget = buildWorkbenchTarget(resume, '/my-work');
      expect(resumeTarget.query.section).toBe('tuning');
      expect(resumeTarget.query.tsStart).toBe('2026-10-13T00:00:00.000Z');
      expect(resumeTarget.query.tsEnd).toBe('2026-10-14T00:00:00.000Z');
      expect(resumeTarget.query.tz).toBe('Asia/Shanghai');
      expect(resumeTarget.query.recordKind).toBe('kpi_snapshot');
      expect(resumeTarget.query.recordId).toBe('rec-1');
      const r = resolveWb360Route(resumeTarget.query);
      expect(r.errors).toEqual([]);
      expect(r.context?.section).toBe('tuning');
      expect(r.context?.window?.timezone).toBe('Asia/Shanghai');
      expect(r.returnRef).toBe('/my-work');
    });

    it('openItem：经路由跳 wb360 single 并记录最近继续（generation 协议）', async () => {
      const mw = useMyWork();
      const item = workItem({ workId: 'inv-1' });
      await mw.openItem(item, '/my-work');
      expect(routerPush).toHaveBeenCalledWith(
        expect.objectContaining({
          path: '/loop/workbench360',
          query: expect.objectContaining({
            from: '/my-work',
            loopId: 'loop-1',
            mode: 'single',
            workId: 'inv-1',
            workKind: 'investigation',
          }),
        }),
      );
      await flushPromises();
      expect(api.putWorkResumeApi).toHaveBeenCalledWith(
        'loop',
        'loop-1',
        expect.objectContaining({ expectedRevision: 0, generation: 3 }),
      );
    });

    it('parseResumeContext：非法项忽略不编造', () => {
      expect(parseResumeContext(null)).toEqual({});
      expect(parseResumeContext({ section: 'bogus' })).toEqual({});
      expect(
        parseResumeContext({ evidenceRefs: [{ id: 1 }, { id: 'x', kind: 'k' }] }),
      ).toEqual({ evidenceRefs: [{ id: 'x', kind: 'k' }] });
    });
  });

  describe('操作入口权限', () => {
    it('SPONSOR 观察角色：不出现认领等可执行入口', async () => {
      setAccount('user-s', ['SPONSOR']);
      const claimData = workData({
        items: [
          workItem({
            nextAction: { kind: 'claim_investigation', label: '认领核查' },
            stage: 'TODO',
            title: '待认领核查 X',
            workId: 'inv-claim',
          }),
        ],
        sourceStatuses: {
          investigation: { inaccessibleCount: 0, status: 'OK', total: 1 },
        },
        total: 1,
      });
      api.getMyWorkApi.mockResolvedValue(claimData);
      const w = mountPage();
      await flushPromises();
      expect(canOperateOnWork(['SPONSOR'])).toBe(false);
      expect(
        w.findAll('.btn-stub').some((b: any) => b.text() === '认领核查'),
      ).toBe(false);
      // SPONSOR 无 wb360 入口权限 → 降级纯文本，不造死链（IAR-D3 术语：回路工作台）
      expect(w.text()).toContain('无回路工作台入口权限');
    });
  });

  describe('断网未保存态（页面徽标）', () => {
    it('设为默认首页保存失败 → 页面显示未保存徽标', async () => {
      api.putWorkspacePreferenceApi.mockRejectedValue(
        new TypeError('Network Error'),
      );
      const w = mountPage();
      await flushPromises();
      const ok = await setDefaultHomePreference('/my-work');
      expect(ok).toBe(false);
      await flushPromises();
      const badge = w.find('[data-testid="pref-unsaved"]');
      expect(badge.exists()).toBe(true);
      expect(badge.text()).toContain('未保存');
    });
  });

  describe('F-G1：偏好同步失败不阻断清单加载', () => {
    it('/me/workspace-preferences 拒绝 → /me/work 仍发起，页面显式偏好不可用提示且清单真实渲染（不假空态）', async () => {
      api.getWorkspacePreferencesApi.mockRejectedValue(
        new TypeError('Network Error'),
      );
      const w = mountPage();
      await flushPromises();
      // 清单请求照常发起（偏好失败不闸断 mount）
      expect(api.getMyWorkApi).toHaveBeenCalledWith(
        expect.objectContaining({ workKind: 'ASSIGNED' }),
      );
      // 显式失败态提示（不伪造、不静默）
      const hint = w.find('[data-testid="pref-sync-failed"]');
      expect(hint.exists()).toBe(true);
      expect(hint.text()).toContain('个人偏好暂不可用');
      // 清单真实渲染（非"暂无分配给你的工作"假空态）
      expect(
        w.find('[data-partition-section="ACTIONABLE"]').text(),
      ).toContain('可行动核查 A');
      expect(w.find('[data-testid="work-total"]').text()).toBe('共 3 条');
    });

    it('偏好恢复后提示消失（失败态不滞留）', async () => {
      api.getWorkspacePreferencesApi.mockRejectedValueOnce(
        new TypeError('Network Error'),
      );
      const w = mountPage();
      await flushPromises();
      expect(w.find('[data-testid="pref-sync-failed"]').exists()).toBe(true);
      // 恢复后手动刷新同步：提示消失
      api.getWorkspacePreferencesApi.mockResolvedValue({
        items: {},
        readAt: '2026-10-14T02:00:00Z',
      });
      const { syncPreferencesFromServer } = await import(
        '#/composables/use-clpm-preferences'
      );
      await syncPreferencesFromServer();
      await flushPromises();
      expect(w.find('[data-testid="pref-sync-failed"]').exists()).toBe(false);
    });
  });
});
