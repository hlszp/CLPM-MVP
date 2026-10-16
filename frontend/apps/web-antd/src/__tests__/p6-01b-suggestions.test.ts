/**
 * P6 Slice I（01b-b）行动建议消费方反例测试（契约 01a §8 A13/A14 +
 * 03§9 P6-01 验收行）。
 *
 * 映射结论：现行实现零缺口，本文件只补反例证据：
 * - 权限不足角色（SPONSOR 观察/经理只读）在系统建议视图：条目可见但
 *   无任何动作按钮（无 wb360 入口 → 纯文本降级，不造死链）；
 * - 工程师（IC/PE 等四操作角色）：系统建议项出现「审核建议」入口，
 *   深链携带 action_item 工作身份 + 处置剖面 + 诊断证据 + from 返回；
 * - 建议审核动作不在 my-work 页内直连（页内无接受/驳回按钮），审核
 *   由 wb360 处置剖面承担（服务端 handling:operate 闸门，SPONSOR/
 *   EXPERT 403 已由后端 test_handling_api.py 锚定）；
 * - diagnosis/overview.vue 的处置建议列为纯展示（actionSuggest 文本
 *   来自服务端 latest JOIN），页内无建议状态变更入口。
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
} from '#/composables/use-clpm-preferences';
import { resolveWb360Route } from '#/views/loop/workbench360/workbench-context';
import MyWorkPage from '#/views/my-work/index.vue';
import {
  buildWorkbenchTarget,
  canOperateOnWork,
} from '#/views/my-work/use-my-work';
import WorkItemList from '#/views/my-work/work-item-list.vue';

/* ── fixtures ── */

function suggestionItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    evidenceRefs: [{ id: 'run-9', kind: 'diagnosis_run' }],
    loopId: 'loop-1',
    loopTagName: 'TIC-101',
    nextAction: { kind: 'review_action_item', label: '审核建议' },
    partition: 'ACTIONABLE',
    priorityReasons: ['HIGH_PRIORITY'],
    reason: '诊断结论：阀门/执行机构问题（证据算子 stiction_ellipse；支持度 0.88）',
    responsibilityPending: false,
    revision: 0,
    sourceKind: 'action_item',
    stage: 'PENDING',
    title: '检查阀门粘滞与非线性：PV-OP 散点呈椭圆回环时优先安排阀门检修',
    unitName: '常减压装置',
    updatedAt: '2026-10-13T08:00:00Z',
    workId: 'ai-1',
    workKind: 'SYSTEM_SUGGESTION',
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

function suggestionData(): WorkData {
  return workData({
    items: [suggestionItem()],
    sourceStatuses: {
      action_item: { inaccessibleCount: 0, status: 'OK', total: 1 },
    },
    total: 1,
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

async function clickTab(wrapper: any, tab: string) {
  await wrapper.find(`[data-tab="${tab}"]`).trigger('click');
  await flushPromises();
}

describe('P6-01b-b 行动建议消费方（反例证据）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    setAccount();
    resetClpmPreferenceState();
    for (const fn of Object.values(api)) fn.mockReset();
    routerPush.mockReset();
    api.getMyWorkApi.mockImplementation((q: any) =>
      Promise.resolve(
        q?.workKind === 'SYSTEM_SUGGESTION' ? suggestionData() : workData(),
      ),
    );
    api.getWorkResumeApi.mockResolvedValue({
      currentGeneration: 1,
      inaccessibleCount: 0,
      items: [],
      readAt: '2026-10-14T01:00:00Z',
      readStatus: 'COMPLETE',
    });
    api.getWorkspacePreferencesApi.mockResolvedValue({
      items: {},
      readAt: '2026-10-14T01:00:00Z',
    });
  });

  describe('权限不足角色（SPONSOR 观察/经理只读）', () => {
    it('系统建议视图：条目只读可见，无任何动作按钮，无 wb360 入口死链', async () => {
      setAccount('user-s', ['SPONSOR']);
      const w = mount(MyWorkPage);
      await flushPromises();
      await clickTab(w, 'SYSTEM_SUGGESTION');

      // 建议条目可见（只读：标题/依据/阶段徽标真实呈现）
      const item = w.find('[data-work-kind="SYSTEM_SUGGESTION"]');
      expect(item.exists()).toBe(true);
      expect(item.text()).toContain('检查阀门粘滞与非线性');
      expect(item.text()).toContain('阀门/执行机构问题');

      // 零动作按钮：无审核/认领/移交/取消关注，也无页内接受/驳回
      const buttons = item.findAll('.btn-stub');
      expect(buttons).toHaveLength(0);
      expect(item.text()).not.toContain('审核建议');
      expect(item.text()).not.toContain('接受');
      expect(item.text()).not.toContain('驳回');
      // 无 wb360 入口权限 → 纯文本降级（不造死链）
      expect(item.text()).toContain('无回路工作台入口权限');
    });

    it('组件级矩阵：canOperate=false 且 canOpenWb360=false → 建议项零按钮', () => {
      const w = mount(WorkItemList, {
        props: {
          actionBusy: {},
          canOpenWb360: false,
          canOperate: false,
          items: [suggestionItem()],
          myUserId: 'user-s',
        },
      });
      expect(w.findAll('.btn-stub')).toHaveLength(0);
      expect(w.text()).toContain('无回路工作台入口权限');
      expect(w.text()).toContain('检查阀门粘滞与非线性');
    });

    it('canOperateOnWork：SPONSOR 不可操作；ADMIN/EXPERT/IC/PE 可操作', () => {
      expect(canOperateOnWork(['SPONSOR'])).toBe(false);
      expect(canOperateOnWork([])).toBe(false);
      for (const r of ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER']) {
        expect(canOperateOnWork([r])).toBe(true);
      }
    });
  });

  describe('工程师（有效权限）系统建议入口', () => {
    it('系统建议视图：IC_ENGINEER 出现「审核建议」入口按钮', async () => {
      setAccount('user-ic', ['IC_ENGINEER']);
      const w = mount(MyWorkPage);
      await flushPromises();
      await clickTab(w, 'SYSTEM_SUGGESTION');
      const item = w.find('[data-work-kind="SYSTEM_SUGGESTION"]');
      const reviewBtn = item
        .findAll('.btn-stub')
        .find((b: any) => b.text() === '审核建议');
      expect(reviewBtn).toBeDefined();
    });

    it('组件级：canOpenWb360=true → 点击「审核建议」emit open（审核在 wb360 处置剖面进行）', async () => {
      const w = mount(WorkItemList, {
        props: {
          actionBusy: {},
          canOpenWb360: true,
          canOperate: true,
          items: [suggestionItem()],
          myUserId: 'user-ic',
        },
      });
      const reviewBtn = w
        .findAll('.btn-stub')
        .find((b: any) => b.text() === '审核建议');
      expect(reviewBtn).toBeDefined();
      await reviewBtn!.trigger('click');
      const emitted = w.emitted('open');
      expect(emitted!.length).toBeGreaterThanOrEqual(1);
      expect((emitted![0]![0] as WorkItem).workId).toBe('ai-1');
      // my-work 页内不提供接受/驳回直连（审核动作由 wb360 + 服务端闸门承担）
      expect(w.text()).not.toContain('接受');
      expect(w.text()).not.toContain('驳回');
    });

    it('深链完整性：action_item 工作身份 + 处置剖面 + 诊断证据 + from，往返还原', () => {
      const item = suggestionItem();
      const { path, query } = buildWorkbenchTarget(item, '/my-work');
      expect(path).toBe('/loop/workbench360');
      expect(query).toEqual({
        from: '/my-work',
        loopId: 'loop-1',
        mode: 'single',
        recordId: 'run-9',
        recordKind: 'diagnosis_run',
        section: 'handling',
        workId: 'ai-1',
        workKind: 'action_item',
      });
      // 消费端往返：上下文与返回位置完整还原（审核入口携带回路与证据）
      const r = resolveWb360Route(query);
      expect(r.errors).toEqual([]);
      expect(r.mode).toBe('single');
      expect(r.context?.loopId).toBe('loop-1');
      expect(r.context?.section).toBe('handling');
      expect(r.context?.workRef).toEqual({ id: 'ai-1', kind: 'action_item' });
      expect(r.context?.evidenceRefs).toEqual([
        { id: 'run-9', kind: 'diagnosis_run' },
      ]);
      expect(r.returnRef).toBe('/my-work');
    });
  });
});
