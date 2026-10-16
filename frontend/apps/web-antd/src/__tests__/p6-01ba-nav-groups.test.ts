/**
 * P6-01b-a（Slice H）五组导航 / IAR-D3 术语 / 驾驶舱「进入工作区」出口测试
 *
 * 覆盖任务书验证口径：
 * - IAR-D1 五组归组/命名快照：11 个可见一级菜单的组归属（meta.navGroup）、
 *   组名（个人责任/全局观察/回路作业/统计分析/系统）与菜单顺序（组内连续、
 *   组间不交错；我的工作 order=-1 保留现状）；
 * - IAR-D3 术语：可见菜单含「回路工作台/系统建议/评估计算任务/诊断计算任务」，
 *   旧称（工作台/关注队列/评估任务/诊断任务）与内部代号（360/wb360）不上界面；
 * - IAR-D6/D8 驾驶舱统一出口：buildWorkzoneExitTarget 经 use-drill 冻结构造点
 *   （wb360DrillQuery）携当前页签上下文（剖面/精确窗/回路页签节点·回路/from），
 *   resolveWb360Route 往返消费不丢参；SPONSOR 按 canOpenWorkbench360 降级纯文本；
 * - 旧书签回归：全部模块既有 path 逐模块锁定（只改归组/命名，不改 path）；
 * - 角色首页与导航一致：ROLE_DEFAULT_HOME 五个落点均为已知路由 path（无死链）。
 */
import type { RouteRecordRaw } from 'vue-router';

import { mount } from '@vue/test-utils';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { routerPush, mockRoute } = vi.hoisted(() => ({
  mockRoute: {
    fullPath: '/cockpit',
    path: '/cockpit',
    query: {} as Record<string, unknown>,
  },
  routerPush: vi.fn(),
}));

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>();
  return {
    ...actual,
    useRoute: () => mockRoute,
    useRouter: () => ({
      currentRoute: { value: mockRoute },
      push: routerPush,
      replace: vi.fn(),
    }),
  };
});

// use-drill 传递依赖的后端接口（纯函数测试不发请求）
vi.mock('#/api/alert', () => ({ getAlertEventsApi: vi.fn() }));
vi.mock('#/api/diagnosis', () => ({ getDiagnosisRunsApi: vi.fn() }));
vi.mock('#/api/handling', () => ({
  getHandlingOrdersApi: vi.fn(),
  getHandlingSuggestionsApi: vi.fn(),
}));
vi.mock('#/api/metric', () => ({ getRankingApi: vi.fn() }));
vi.mock('#/api/tuning', () => ({ getTuningTasksApi: vi.fn() }));
vi.mock('#/api/cockpit', () => ({
  getBackendAccessRolesApi: vi.fn(async () => ({ roles: [] })),
}));

// store/auth 传递依赖（本文件只消费 ROLE_DEFAULT_HOME 常量）
vi.mock('@vben/preferences', () => ({
  preferences: { app: { defaultHomePath: '/cockpit' } },
}));
vi.mock('@vben/constants', () => ({ LOGIN_PATH: '/auth/login' }));
vi.mock('ant-design-vue', () => ({
  message: { warning: vi.fn() },
  notification: { error: vi.fn(), success: vi.fn() },
}));
vi.mock('#/locales', () => ({ $t: (key: string) => key }));
vi.mock('#/api', () => ({
  getAccessCodesApi: vi.fn(),
  getUserInfoApi: vi.fn(),
  loginApi: vi.fn(),
  logoutApi: vi.fn(),
}));

import { useUserStore } from '@vben/stores';

import alertRoutes from '#/router/routes/modules/alert';
import assessRoutes from '#/router/routes/modules/assess';
import clpmRoutes from '#/router/routes/modules/clpm';
import cockpitRoutes from '#/router/routes/modules/cockpit';
import configRoutes from '#/router/routes/modules/config';
import diagnosisRoutes from '#/router/routes/modules/diagnosis';
import handlingRoutes from '#/router/routes/modules/handling';
import loopRoutes from '#/router/routes/modules/loop';
import loopWorkbenchRoutes from '#/router/routes/modules/loop-workbench';
import monitorRoutes from '#/router/routes/modules/monitor';
import reportsRoutes from '#/router/routes/modules/reports';
import systemRoutes from '#/router/routes/modules/system';
import taskRoutes from '#/router/routes/modules/task';
import tuningRoutes from '#/router/routes/modules/tuning';
import vbenRoutes from '#/router/routes/modules/vben';
import {
  NAV_GROUP_LABELS,
  NAV_GROUPS,
} from '#/router/routes/nav-groups';
import { useCockpitStore } from '#/store/cockpit';
import CockpitHeader from '#/views/cockpit/components/cockpit-header.vue';
import {
  buildWorkzoneExitTarget,
  COCKPIT_WORKZONE_SECTION,
} from '#/views/cockpit/utils/workzone-exit';
import { localTimezone } from '#/views/cockpit/wb-comps/use-drill';
import { resolveWb360Route } from '#/views/loop/workbench360/workbench-context';

const { ROLE_DEFAULT_HOME } = await import('#/store/auth');

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

const ALL_MODULE_ROUTES: { module: string; routes: RouteRecordRaw[] }[] = [
  { module: 'alert', routes: alertRoutes },
  { module: 'assess', routes: assessRoutes },
  { module: 'clpm', routes: clpmRoutes },
  { module: 'cockpit', routes: cockpitRoutes },
  { module: 'config', routes: configRoutes },
  { module: 'diagnosis', routes: diagnosisRoutes },
  { module: 'handling', routes: handlingRoutes },
  { module: 'loop', routes: loopRoutes },
  { module: 'loop-workbench', routes: loopWorkbenchRoutes },
  { module: 'monitor', routes: monitorRoutes },
  { module: 'reports', routes: reportsRoutes },
  { module: 'system', routes: systemRoutes },
  { module: 'task', routes: taskRoutes },
  { module: 'tuning', routes: tuningRoutes },
  { module: 'vben', routes: vbenRoutes },
];

function flattenRoutes(
  routes: RouteRecordRaw[],
  out: RouteRecordRaw[] = [],
): RouteRecordRaw[] {
  for (const r of routes) {
    out.push(r);
    if (r.children) flattenRoutes(r.children as RouteRecordRaw[], out);
  }
  return out;
}

/** 全部模块顶层可见菜单（hideInMenu 的不进菜单） */
function visibleTopMenus(): RouteRecordRaw[] {
  return ALL_MODULE_ROUTES.flatMap((m) => m.routes).filter(
    (r) => r.meta && !r.meta.hideInMenu,
  );
}

/** 可见菜单标题（顶层 + 递归可见子级） */
function visibleMenuTitles(): string[] {
  const titles: string[] = [];
  const walk = (routes: RouteRecordRaw[]) => {
    for (const r of routes) {
      if (r.meta?.hideInMenu) continue;
      if (typeof r.meta?.title === 'string' && r.meta.title) {
        titles.push(r.meta.title);
      }
      if (r.children) walk(r.children as RouteRecordRaw[]);
    }
  };
  walk(ALL_MODULE_ROUTES.flatMap((m) => m.routes));
  return titles;
}

// ---------------------------------------------------------------------------
// IAR-D1 五组归组/命名快照
// ---------------------------------------------------------------------------
describe('IAR-D1 五组导航归组与命名（Slice H）', () => {
  it('五组登记：组名按已采纳口径且菜单出现顺序固定', () => {
    expect(NAV_GROUPS.map((g) => g.label)).toEqual([
      '个人责任',
      '全局观察',
      '回路作业',
      '统计分析',
      '系统',
    ]);
  });

  it('可见一级菜单 = 11 项，组归属/命名/顺序快照（我的工作 order=-1 保留）', () => {
    const view = visibleTopMenus()
      .map((r) => ({
        group: (r.meta?.navGroup as string | undefined) ?? null,
        groupLabel: NAV_GROUP_LABELS[
          r.meta?.navGroup as keyof typeof NAV_GROUP_LABELS
        ] as string | undefined,
        order: (r.meta?.order as number | undefined) ?? 999,
        path: r.path,
        title: r.meta?.title as string,
      }))
      .toSorted((a, b) => a.order - b.order);
    expect(view).toEqual([
      { group: 'personal', groupLabel: '个人责任', order: -1, path: '/my-work', title: '我的工作' },
      { group: 'observe', groupLabel: '全局观察', order: 0, path: '/cockpit', title: '驾驶舱' },
      { group: 'loop-ops', groupLabel: '回路作业', order: 1, path: '/loop/workbench360', title: '回路工作台' },
      { group: 'loop-ops', groupLabel: '回路作业', order: 2, path: '/monitor', title: '回路监控' },
      { group: 'loop-ops', groupLabel: '回路作业', order: 3, path: '/assess', title: '性能评估' },
      { group: 'loop-ops', groupLabel: '回路作业', order: 4, path: '/diagnosis', title: '回路诊断' },
      { group: 'loop-ops', groupLabel: '回路作业', order: 5, path: '/tuning', title: '参数整定' },
      { group: 'loop-ops', groupLabel: '回路作业', order: 6, path: '/handling', title: '运维处置' },
      { group: 'analytics', groupLabel: '统计分析', order: 7, path: '/reports', title: '统计分析' },
      { group: 'system', groupLabel: '系统', order: 8, path: '/config', title: '参数配置' },
      { group: 'system', groupLabel: '系统', order: 9, path: '/system', title: '系统管理' },
    ]);
  });

  it('每个可见一级菜单均有有效 navGroup，且组间顺序连续不交错', () => {
    const menus = visibleTopMenus();
    expect(menus.length).toBeGreaterThan(0);
    const keys = new Set(NAV_GROUPS.map((g) => g.key));
    const sorted = menus.toSorted(
      (a, b) =>
        ((a.meta?.order as number | undefined) ?? 999) -
        ((b.meta?.order as number | undefined) ?? 999),
    );
    const groupSeq: number[] = [];
    for (const r of sorted) {
      const key = r.meta?.navGroup as string | undefined;
      expect(key && keys.has(key as never), `${r.path} 缺有效 navGroup`).toBe(
        true,
      );
      groupSeq.push(NAV_GROUPS.findIndex((g) => g.key === key));
    }
    for (let i = 1; i < groupSeq.length; i++) {
      // 组索引非递减 = 组内连续、组间不交错
      expect(groupSeq[i]! >= groupSeq[i - 1]!).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// IAR-D3 术语（内部代号不上界面）
// ---------------------------------------------------------------------------
describe('IAR-D3 菜单术语（Slice H）', () => {
  it('新术语在可见菜单中生效：回路工作台/系统建议/评估计算任务/诊断计算任务', () => {
    const titles = visibleMenuTitles();
    expect(titles).toContain('回路工作台');
    expect(titles).toContain('系统建议');
    expect(titles).toContain('评估计算任务');
    expect(titles).toContain('诊断计算任务');
  });

  it('旧称与内部代号不出现在可见菜单', () => {
    const titles = visibleMenuTitles();
    // 旧称（精确相等，新名为其超集不误伤）
    expect(titles).not.toContain('工作台');
    expect(titles).not.toContain('关注队列');
    expect(titles).not.toContain('评估任务');
    expect(titles).not.toContain('诊断任务');
    // 内部代号（wb360/工作台360/Workbench360）不上界面
    for (const t of titles) {
      expect(t, `菜单「${t}」含内部代号`).not.toMatch(/360/i);
    }
  });
});

// ---------------------------------------------------------------------------
// IAR-D6/D8 出口目标构造（经 use-drill 冻结构造点 wb360DrillQuery）
// ---------------------------------------------------------------------------
describe('IAR-D6/D8 进入工作区出口目标构造（经冻结构造点）', () => {
  const NOW = new Date('2026-10-14T06:00:00.000Z');

  it('回路页签：选中节点+回路 → single 直达，携 loopId/nodeId/精确窗/from', () => {
    const { path, query } = buildWorkzoneExitTarget({
      from: '/cockpit/loops?node=node-7&loop=loop-9',
      loopId: 'loop-9',
      nodeId: 'node-7',
      now: NOW,
      tabPath: '/cockpit/loops',
      timeWindow: '24h',
    });
    expect(path).toBe('/loop/workbench360');
    expect(query).toEqual({
      from: '/cockpit/loops?node=node-7&loop=loop-9',
      loopId: 'loop-9',
      mode: 'single',
      nodeId: 'node-7',
      tsEnd: '2026-10-14T06:00:00.000Z',
      tsStart: '2026-10-13T06:00:00.000Z',
      tz: TZ,
    });
    // 往返：Adapter 无错误消费，上下文不丢参
    const r = resolveWb360Route(query);
    expect(r.errors).toEqual([]);
    expect(r.mode).toBe('single');
    expect(r.context?.loopId).toBe('loop-9');
    expect(r.context?.scope?.nodeId).toBe('node-7');
    expect(r.context?.window?.tsStart).toBe('2026-10-13T06:00:00.000Z');
    expect(r.context?.window?.timezone).toBe(TZ);
    expect(r.returnRef).toBe('/cockpit/loops?node=node-7&loop=loop-9');
  });

  it('回路页签：仅选节点 → all 模式 + nodeId scope（无 loopId 不造对象）', () => {
    const { query } = buildWorkzoneExitTarget({
      from: '/cockpit/loops',
      loopId: null,
      nodeId: 'node-7',
      now: NOW,
      tabPath: '/cockpit/loops',
      timeWindow: '7d',
    });
    expect(query.mode).toBe('all');
    expect(query.nodeId).toBe('node-7');
    expect(query.loopId).toBeUndefined();
    expect(query.section).toBeUndefined();
    const r = resolveWb360Route(query);
    expect(r.errors).toEqual([]);
    expect(r.mode).toBe('all');
    expect(r.context).toBeNull();
    expect(r.carried.scope?.nodeId).toBe('node-7');
    expect(r.carried.window?.tsEnd).toBe('2026-10-14T06:00:00.000Z');
  });

  it('诊断/整定/处置页签 → 对应剖面；总览/性能默认 assess 不显式携带', () => {
    const sectionOf = (tab: string) =>
      buildWorkzoneExitTarget({
        from: tab,
        now: NOW,
        tabPath: tab,
        timeWindow: '24h',
      }).query;
    expect(sectionOf('/cockpit/diagnosis').section).toBe('diagnosis');
    expect(sectionOf('/cockpit/tuning').section).toBe('tuning');
    expect(sectionOf('/cockpit/handling').section).toBe('handling');
    expect(sectionOf('/cockpit').section).toBeUndefined();
    expect(sectionOf('/cockpit/performance').section).toBeUndefined();
    // 剖面映射表与六页签一一对应（其余页签缺省）
    expect(Object.keys(COCKPIT_WORKZONE_SECTION).toSorted()).toEqual([
      '/cockpit/diagnosis',
      '/cockpit/handling',
      '/cockpit/tuning',
    ]);
    // 往返：诊断页签 → carried.section 保留待选中回路后消费
    const r = resolveWb360Route(sectionOf('/cockpit/diagnosis'));
    expect(r.errors).toEqual([]);
    expect(r.carried.section).toBe('diagnosis');
  });

  it('精确时间窗：24h/7d/30d 换算为等长精确起止（禁近似滚动窗）', () => {
    const HOUR = 3_600_000;
    for (const [win, hours] of [
      ['24h', 24],
      ['7d', 168],
      ['30d', 720],
    ] as const) {
      const { query } = buildWorkzoneExitTarget({
        from: '/cockpit',
        now: NOW,
        tabPath: '/cockpit',
        timeWindow: win,
      });
      const span = Date.parse(query.tsEnd!) - Date.parse(query.tsStart!);
      expect(span).toBe(hours * HOUR);
      expect(query.tz).toBe(localTimezone());
    }
  });

  it('非回路页签不携带节点/回路（页内瞬态选中不泄漏为工作区上下文）', () => {
    const { query } = buildWorkzoneExitTarget({
      from: '/cockpit',
      loopId: 'loop-stale',
      nodeId: 'node-stale',
      now: NOW,
      tabPath: '/cockpit',
      timeWindow: '24h',
    });
    expect(query.mode).toBe('all');
    expect(query.loopId).toBeUndefined();
    expect(query.nodeId).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 驾驶舱头区出口按钮：权限降级与点击跳转（SPONSOR 纯文本）
// ---------------------------------------------------------------------------
describe('驾驶舱头区「进入工作区」出口（IAR-D6/D8，按角色落地）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    routerPush.mockReset();
    mockRoute.path = '/cockpit';
    mockRoute.fullPath = '/cockpit';
    mockRoute.query = {};
  });

  function mountHeader(tabPath: string, roles: string[]) {
    mockRoute.path = tabPath;
    mockRoute.fullPath = tabPath;
    useUserStore().setUserInfo({ roles } as never);
    return mount(CockpitHeader, {
      global: {
        mocks: { $route: mockRoute },
        stubs: {
          IconifyIcon: { template: '<span class="icon-stub" />' },
          RouterLink: { template: '<a class="tab-stub"><slot /></a>' },
        },
      },
    });
  }

  it('SPONSOR（无 wb360 权限）→ 降级纯文本，不渲染可点击按钮', () => {
    const w = mountHeader('/cockpit', ['SPONSOR']);
    expect(w.find('[data-testid="workzone-exit"]').exists()).toBe(false);
    const plain = w.find('[data-testid="workzone-exit-plain"]');
    expect(plain.exists()).toBe(true);
    expect(plain.text()).toBe('进入工作区');
    w.unmount();
  });

  it('空角色 → 同样降级纯文本（不造死链）', () => {
    const w = mountHeader('/cockpit', []);
    expect(w.find('[data-testid="workzone-exit"]').exists()).toBe(false);
    expect(w.find('[data-testid="workzone-exit-plain"]').exists()).toBe(true);
    w.unmount();
  });

  it('ADMIN 在诊断页签点击 → 经冻结构造点跳 wb360（all + section=diagnosis + 精确窗 + from）', async () => {
    const w = mountHeader('/cockpit/diagnosis', ['ADMIN']);
    const btn = w.find('[data-testid="workzone-exit"]');
    expect(btn.exists()).toBe(true);
    await btn.trigger('click');
    expect(routerPush).toHaveBeenCalledTimes(1);
    const arg = routerPush.mock.calls[0]![0] as {
      path: string;
      query: Record<string, string>;
    };
    expect(arg.path).toBe('/loop/workbench360');
    expect(arg.query.mode).toBe('all');
    expect(arg.query.section).toBe('diagnosis');
    expect(arg.query.from).toBe('/cockpit/diagnosis');
    expect(arg.query.tsStart).toEqual(expect.any(String));
    expect(arg.query.tsEnd).toEqual(expect.any(String));
    expect(arg.query.tz).toBe(TZ);
    w.unmount();
  });

  it('IC_ENGINEER 在回路页签（已选节点+回路）点击 → single 直达携 loopId/nodeId', async () => {
    const cockpitStore = useCockpitStore();
    cockpitStore.setLoopsNode('node-7');
    cockpitStore.selectLoop('loop-9');
    const w = mountHeader('/cockpit/loops', ['IC_ENGINEER']);
    await w.find('[data-testid="workzone-exit"]').trigger('click');
    const arg = routerPush.mock.calls[0]![0] as {
      query: Record<string, string>;
    };
    expect(arg.query.mode).toBe('single');
    expect(arg.query.loopId).toBe('loop-9');
    expect(arg.query.nodeId).toBe('node-7');
    expect(arg.query.from).toBe('/cockpit/loops');
    w.unmount();
  });
});

// ---------------------------------------------------------------------------
// 旧书签回归：既有 path 逐模块锁定（只改归组/命名，不改 path）
// ---------------------------------------------------------------------------
describe('既有 path 未变回归（旧书签/报告链接）', () => {
  const EXPECTED: Record<string, string[]> = {
    alert: ['/alert', '/alert/events', '/alert/rules'],
    assess: [
      '/assess',
      '/metric/pid-dashboard',
      '/metric/indicator-analysis',
      '/metric/loop-evaluation',
      '/metric/tasks',
      '/metric/loop-performance',
      '/metric/history',
      '/metric/matrix',
      '/metric',
      '/metric/kpi-report',
    ],
    clpm: ['/workbench'],
    cockpit: [
      '/my-work',
      '/cockpit',
      '/cockpit/loops',
      '/cockpit/performance',
      '/cockpit/diagnosis',
      '/cockpit/tuning',
      '/cockpit/handling',
    ],
    config: [
      '/config',
      '/config/link',
      '/config/tag',
      '/config/loop',
      '/config/ledger',
      '/config/factory',
      '/config/datasource',
      '/config/metric',
      '/config/diagnosis',
      '/config/alert-rules',
      '/config/pid-template',
      '/config/eng-templates',
      '/loop/aas-sync',
      '/tag/list',
      '/tag',
      '/loop/manage',
      '/loop/factory',
      '/loop/ledger',
      '/loop/data',
      '/metric/config',
      '/diagnosis/config',
      '/system/pid-template',
    ],
    diagnosis: [
      '/diagnosis',
      '/diagnosis/workbench',
      '/diagnosis/overview',
      '/diagnosis/tasks',
      '/diagnosis/records',
    ],
    handling: [
      '/handling',
      '/handling/suggestions',
      '/handling/tasks',
      '/handling/orders',
      '/handling/archive',
      '/handling/workbench',
      '/handling/statistics',
    ],
    loop: [
      '/loop',
      '/loop/workbench',
      '/monitor/loop-workbench',
      '/loop/detail/:id',
    ],
    'loop-workbench': ['/loop/workbench360'],
    monitor: [
      '/monitor',
      '/monitor/loops',
      '/monitor/alerts',
      '/monitor/attention',
      '/loop/monitor',
      '/loop/monitor/legacy',
      '/dashboard',
      '/dashboard/workbench',
    ],
    reports: [
      '/reports',
      '/reports/performance',
      '/reports/data-quality',
      '/reports/alert-statistics',
      '/reports/diagnosis',
      '/reports/handling',
      '/reports/benefit',
      '/reports/subscription',
      '/reports/overview',
    ],
    system: [
      '/system',
      '/system/basic-info',
      '/system/users',
      '/system/audit',
      '/system/dict',
      '/system/permissions',
      '/system/modules',
      '/system/reports',
      '/system/algorithm-params',
    ],
    task: ['/tasks/:taskId'],
    tuning: [
      '/tuning',
      '/tuning/overview',
      '/tuning/workbench',
      '/tuning/records',
      '/tuning/verification',
    ],
    vben: ['/profile'],
  };

  it.each(ALL_MODULE_ROUTES.map((m) => [m.module, m.routes] as const))(
    '%s 模块 path 集合未变',
    (module, routes) => {
      const paths = flattenRoutes(routes as RouteRecordRaw[]).map((r) => r.path);
      expect(paths).toEqual(EXPECTED[module]);
    },
  );

  it('全部模块覆盖（无新增/遗漏模块文件）', () => {
    expect(Object.keys(EXPECTED).toSorted()).toEqual(
      ALL_MODULE_ROUTES.map((m) => m.module).toSorted(),
    );
  });
});

// ---------------------------------------------------------------------------
// 角色首页与导航一致（G+整合已落地；本 Slice 验证无死链，不重做）
// ---------------------------------------------------------------------------
describe('角色首页与导航一致（无死链）', () => {
  it('ROLE_DEFAULT_HOME 五个落点均为已知路由 path', () => {
    const knownPaths = new Set(
      ALL_MODULE_ROUTES.flatMap((m) =>
        flattenRoutes(m.routes).map((r) => r.path),
      ),
    );
    expect(Object.keys(ROLE_DEFAULT_HOME).toSorted()).toEqual([
      'ADMIN',
      'EXPERT',
      'IC_ENGINEER',
      'PE_ENGINEER',
      'SPONSOR',
    ]);
    for (const [role, home] of Object.entries(ROLE_DEFAULT_HOME)) {
      expect(knownPaths.has(home), `${role} 首页 ${home} 不是已知路由`).toBe(
        true,
      );
    }
  });

  it('角色首页口径保持：IC/PE/EXPERT→/my-work，ADMIN/SPONSOR→/cockpit', () => {
    expect(ROLE_DEFAULT_HOME.IC_ENGINEER).toBe('/my-work');
    expect(ROLE_DEFAULT_HOME.PE_ENGINEER).toBe('/my-work');
    expect(ROLE_DEFAULT_HOME.EXPERT).toBe('/my-work');
    expect(ROLE_DEFAULT_HOME.ADMIN).toBe('/cockpit');
    expect(ROLE_DEFAULT_HOME.SPONSOR).toBe('/cockpit');
  });
});
