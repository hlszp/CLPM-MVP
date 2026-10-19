/**
 * P6-01b-a（Slice H）五组导航 / IAR-D3 术语测试
 *
 * 覆盖任务书验证口径：
 * - IAR-D1 五组归组/命名快照：11 个可见一级菜单的组归属（meta.navGroup）、
 *   组名（个人责任/全局观察/回路作业/统计分析/系统）与菜单顺序（组内连续、
 *   组间不交错；我的工作 order=-1 保留现状）；
 * - IAR-D3 术语：可见菜单含「回路工作台/系统建议/评估任务/诊断任务」，
 *   旧称（工作台/关注队列）与内部代号（360/wb360）不上界面；
 * - 旧书签回归：全部模块既有 path 逐模块锁定（只改归组/命名，不改 path）；
 * - 角色首页与导航一致：ROLE_DEFAULT_HOME 五个落点均为已知路由 path（无死链）。
 * （驾驶舱「进入工作区」按钮已于 2026-10-19 按用户要求删除，相关用例一并移除）
 */
import type { RouteRecordRaw } from 'vue-router';

import { describe, expect, it, vi } from 'vitest';

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

const { ROLE_DEFAULT_HOME } = await import('#/store/auth');

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
  it('新术语在可见菜单中生效：回路工作台/系统建议/评估任务/诊断任务', () => {
    const titles = visibleMenuTitles();
    expect(titles).toContain('回路工作台');
    expect(titles).toContain('系统建议');
    expect(titles).toContain('评估任务');
    expect(titles).toContain('诊断任务');
  });

  it('旧称与内部代号不出现在可见菜单', () => {
    const titles = visibleMenuTitles();
    // 旧称（精确相等，新名为其超集不误伤）
    expect(titles).not.toContain('工作台');
    expect(titles).not.toContain('关注队列');
    // 内部代号（wb360/工作台360/Workbench360）不上界面
    for (const t of titles) {
      expect(t, `菜单「${t}」含内部代号`).not.toMatch(/360/i);
    }
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
