/**
 * 路由权限三方对齐单元测试（基准 = 实现契约 §5 + UI/UX §4.2）
 *
 * 覆盖 2026-07-28 权限收紧项：
 * - /system/reports 仅 ADMIN（后端 reports.py 全端点仅 ADMIN）
 * - /loop/aas-sync 仅 ADMIN（后端 datasource.py/dcs.py 写端点仅 ADMIN）
 * - EXPERT 不可见工作台/性能评估（仅诊断中心 + 回路整定）
 * - 诊断中心 / 回路整定对 EXPERT 放行
 */
import type { RouteRecordRaw } from 'vue-router';

import { describe, expect, it } from 'vitest';

import dashboardRoutes from '#/router/routes/modules/dashboard';
import diagnosisRoutes from '#/router/routes/modules/diagnosis';
import loopRoutes from '#/router/routes/modules/loop';
import metricRoutes from '#/router/routes/modules/metric';
import systemRoutes from '#/router/routes/modules/system';
import tuningRoutes from '#/router/routes/modules/tuning';

function findRoute(
  routes: RouteRecordRaw[],
  predicate: (r: RouteRecordRaw) => boolean,
): RouteRecordRaw | undefined {
  for (const r of routes) {
    if (predicate(r)) return r;
    const found = r.children ? findRoute(r.children, predicate) : undefined;
    if (found) return found;
  }
  return undefined;
}

function authorityOf(route: RouteRecordRaw): string[] {
  return (route.meta?.authority as string[]) ?? [];
}

describe('路由权限三方对齐（实现契约 §5 + UI/UX §4.2）', () => {
  it('/system/reports 收紧为仅 ADMIN', () => {
    const route = findRoute(systemRoutes, (r) => r.path === '/system/reports');
    expect(route).toBeDefined();
    expect(authorityOf(route!)).toEqual(['ADMIN']);
  });

  it('/loop/aas-sync 收紧为仅 ADMIN', () => {
    const route = findRoute(loopRoutes, (r) => r.path === '/loop/aas-sync');
    expect(route).toBeDefined();
    expect(authorityOf(route!)).toEqual(['ADMIN']);
  });

  it('工作台（/dashboard）排除 EXPERT', () => {
    const workbench = findRoute(
      dashboardRoutes,
      (r) => r.path === '/dashboard/workbench',
    );
    expect(workbench).toBeDefined();
    expect(authorityOf(workbench!)).not.toContain('EXPERT');
    expect(authorityOf(workbench!)).toContain('SPONSOR');
  });

  it('性能评估（/metric）所有可见子路由排除 EXPERT、放行 SPONSOR', () => {
    const parent = metricRoutes[0]!;
    expect(authorityOf(parent)).not.toContain('EXPERT');
    for (const child of parent.children ?? []) {
      const auth = authorityOf(child);
      expect(auth.length).toBeGreaterThan(0);
      expect(auth).not.toContain('EXPERT');
      // 评估任务/指标配置本就只对 ADMIN/IC_ENGINEER 开放，无需校验 SPONSOR
      if (
        [
          '/metric/pid-dashboard',
          '/metric/loop-performance',
          '/metric/kpi-report',
        ].includes(child.path)
      ) {
        expect(auth).toContain('SPONSOR');
      }
    }
  });

  it('诊断中心对 EXPERT 放行', () => {
    const parent = diagnosisRoutes[0]!;
    expect(authorityOf(parent)).toContain('EXPERT');
    for (const path of [
      '/diagnosis/overview',
      '/diagnosis/tasks',
      '/diagnosis/records',
      '/diagnosis/tracker',
    ]) {
      const route = findRoute(diagnosisRoutes, (r) => r.path === path);
      expect(route, path).toBeDefined();
      expect(authorityOf(route!)).toContain('EXPERT');
    }
  });

  it('回路整定对 EXPERT 放行', () => {
    const parent = tuningRoutes[0]!;
    expect(authorityOf(parent)).toContain('EXPERT');
    for (const child of parent.children ?? []) {
      expect(authorityOf(child)).toContain('EXPERT');
    }
  });
});
