/**
 * P4-02 页面码路由过滤单元测试（菜单按可达页生成 · 五角色矩阵）
 *
 * 事实源：后端 services/authz_catalog.ROLE_SEED 的页面码集合（/auth/me
 * pageCodes 下发）+ 前端路由 meta.permCode 映射。验证：
 *
 * - 未授权页面码 → 路由整棵移除（菜单 + 直链同步受限）
 * - 纯菜单父路由在全部子路由移除后一并移除（父入口不残留不可达项）
 * - 五角色种子矩阵关键差异点（SPONSOR 无工作台/系统管理；EXPERT 无
 *   收益报告；IC 无系统管理但有配置域；ADMIN 全量）
 * - 移除一个授权（page:config.tag）→ 对应入口同步消失（收紧生效）
 */
import type { RouteRecordRaw } from 'vue-router';

import { describe, expect, it } from 'vitest';

import { filterTreeByPageCodes } from '#/router/routes';
import cockpitRoutes from '#/router/routes/modules/cockpit';
import configRoutes from '#/router/routes/modules/config';
import loopWorkbenchRoutes from '#/router/routes/modules/loop-workbench';
import monitorRoutes from '#/router/routes/modules/monitor';
import reportsRoutes from '#/router/routes/modules/reports';
import systemRoutes from '#/router/routes/modules/system';

/** 五角色种子页面码镜像（与后端 ROLE_SEED _PAGE_CODES 一致；断言关键成员） */
const COMMON_COCKPIT = [
  'page:cockpit.overview',
  'page:cockpit.loops',
  'page:cockpit.performance',
  'page:cockpit.diagnosis',
  'page:cockpit.tuning',
  'page:cockpit.handling',
];
const MONITOR_COMMON = [
  'page:monitor.loops',
  'page:monitor.alerts',
  'page:monitor.attention',
];
const CONFIG_ENG = [
  'page:config.tag',
  'page:config.loop',
  'page:config.ledger',
  'page:config.factory',
  'page:config.datasource',
];
const SYSTEM_ALL = [
  'page:system.basic-info',
  'page:system.users',
  'page:system.audit',
  'page:system.dict',
  'page:system.permissions',
  'page:system.modules',
  'page:system.reports',
  'page:system.algorithm-params',
];

const REPORTS_COMMON = [
  'page:reports.performance',
  'page:reports.data-quality',
  'page:reports.alert-statistics',
  'page:reports.diagnosis',
  'page:reports.handling',
];

const SEED_PAGES: Record<string, string[]> = {
  ADMIN: [
    ...COMMON_COCKPIT,
    'page:workbench.main',
    ...MONITOR_COMMON,
    'page:monitor.table',
    'page:alert.rules',
    'page:assess.dashboard',
    'page:assess.indicator',
    'page:assess.loop-evaluation',
    'page:assess.performance',
    'page:assess.tasks',
    'page:assess.history',
    'page:assess.matrix',
    'page:assess.kpi-report',
    'page:task.detail',
    ...CONFIG_ENG,
    'page:config.link',
    'page:config.metric',
    'page:config.diagnosis',
    'page:config.alert-rules',
    'page:config.pid-template',
    ...SYSTEM_ALL,
    'page:reports.performance',
    'page:reports.data-quality',
    'page:reports.alert-statistics',
    'page:reports.diagnosis',
    'page:reports.handling',
    'page:reports.benefit',
    'page:reports.subscription',
  ],
  EXPERT: [
    ...COMMON_COCKPIT,
    'page:workbench.main',
    ...MONITOR_COMMON,
    ...REPORTS_COMMON,
  ],
  IC_ENGINEER: [
    ...COMMON_COCKPIT,
    'page:workbench.main',
    ...MONITOR_COMMON,
    'page:monitor.table',
    ...CONFIG_ENG,
    'page:task.detail',
    ...REPORTS_COMMON,
    'page:reports.benefit',
  ],
  PE_ENGINEER: [
    ...COMMON_COCKPIT,
    'page:workbench.main',
    ...MONITOR_COMMON,
    'page:monitor.table',
    ...CONFIG_ENG,
    'page:task.detail',
    ...REPORTS_COMMON,
    'page:reports.benefit',
  ],
  SPONSOR: [
    ...COMMON_COCKPIT,
    ...MONITOR_COMMON,
    ...REPORTS_COMMON,
    'page:reports.benefit',
  ],
};

function grantedOf(role: string): Set<string> {
  return new Set(SEED_PAGES[role]);
}

function pathsOf(routes: RouteRecordRaw[]): string[] {
  const out: string[] = [];
  for (const r of routes) {
    out.push(r.path);
    if (r.children) out.push(...pathsOf(r.children as RouteRecordRaw[]));
  }
  return out;
}

function hasPath(routes: RouteRecordRaw[], path: string): boolean {
  return pathsOf(routes).includes(path);
}

describe('P4-02 页面码路由过滤（五角色矩阵 × 菜单/直链）', () => {
  it('SPONSOR：监控/驾驶舱可达，工作台与配置/系统管理整树移除', () => {
    const granted = grantedOf('SPONSOR');
    expect(
      hasPath(filterTreeByPageCodes(monitorRoutes, granted), '/monitor/loops'),
    ).toBe(true);
    expect(
      hasPath(filterTreeByPageCodes(cockpitRoutes, granted), '/cockpit'),
    ).toBe(true);
    // 工作台（page:workbench.main 未授权）移除
    expect(
      hasPath(
        filterTreeByPageCodes(loopWorkbenchRoutes, granted),
        '/loop/workbench360',
      ),
    ).toBe(false);
    // 配置父树：SPONSOR 无任何配置页 → 父路由 /config 一并移除（父入口不残留）
    const config = filterTreeByPageCodes(configRoutes, granted);
    expect(hasPath(config, '/config')).toBe(false);
    // 系统管理父树同理
    const system = filterTreeByPageCodes(systemRoutes, granted);
    expect(hasPath(system, '/system')).toBe(false);
    expect(hasPath(system, '/system/permissions')).toBe(false);
  });

  it('EXPERT：工作台可达，收益报告移除（种子无 page:reports.benefit）', () => {
    const granted = grantedOf('EXPERT');
    expect(
      hasPath(
        filterTreeByPageCodes(loopWorkbenchRoutes, granted),
        '/loop/workbench360',
      ),
    ).toBe(true);
    const reports = filterTreeByPageCodes(reportsRoutes, granted);
    expect(hasPath(reports, '/reports/benefit')).toBe(false);
    expect(hasPath(reports, '/reports/performance')).toBe(true);
  });

  it('IC_ENGINEER：配置域工程页可达，系统管理整树移除', () => {
    const granted = grantedOf('IC_ENGINEER');
    const config = filterTreeByPageCodes(configRoutes, granted);
    expect(hasPath(config, '/config')).toBe(true);
    expect(hasPath(config, '/config/tag')).toBe(true);
    // 指标配置/预警规则/PID 模板为 ADMIN 专属页码，IC 移除
    expect(hasPath(config, '/config/metric')).toBe(false);
    expect(hasPath(config, '/config/alert-rules')).toBe(false);
    const system = filterTreeByPageCodes(systemRoutes, granted);
    expect(hasPath(system, '/system')).toBe(false);
  });

  it('ADMIN：全量页面可达（含系统管理与订阅配置）', () => {
    const granted = grantedOf('ADMIN');
    const system = filterTreeByPageCodes(systemRoutes, granted);
    expect(hasPath(system, '/system')).toBe(true);
    expect(hasPath(system, '/system/users')).toBe(true);
    expect(hasPath(system, '/system/permissions')).toBe(true);
    const reports = filterTreeByPageCodes(reportsRoutes, granted);
    expect(hasPath(reports, '/reports/subscription')).toBe(true);
  });

  it('移除一个授权（page:config.tag）→ 配置入口对 IC 同步受限', () => {
    const granted = grantedOf('IC_ENGINEER');
    granted.delete('page:config.tag');
    const config = filterTreeByPageCodes(configRoutes, granted);
    expect(hasPath(config, '/config/tag')).toBe(false);
    // 其余配置页不受影响（父树仍有存留子路由）
    expect(hasPath(config, '/config/loop')).toBe(true);
    expect(hasPath(config, '/config')).toBe(true);
  });

  it('未挂 permCode 的路由（legacy redirect/书签）不受页面码过滤影响', () => {
    const granted = grantedOf('SPONSOR');
    // DashboardLegacy 等重定向路由无 permCode → 保留（仍由 authority 过滤）
    const monitor = filterTreeByPageCodes(monitorRoutes, granted);
    expect(hasPath(monitor, '/dashboard')).toBe(true);
  });
});
