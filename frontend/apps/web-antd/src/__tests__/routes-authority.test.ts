/**
 * 路由权限三方对齐单元测试（基准 = 实现契约 §5 + UI/UX §4.2）
 *
 * 覆盖 2026-07-28 权限收紧项（IA 重构 Phase A 路径同步）：
 * - /system/reports 仅 ADMIN（后端 reports.py 全端点仅 ADMIN）
 * - /config/link（原 /loop/aas-sync）仅 ADMIN（后端 datasource.py/dcs.py 写端点仅 ADMIN）
 * - EXPERT 不可见监控/评估（仅诊断 + 整定）
 * - 诊断 / 整定对 EXPERT 放行
 */
import type { RouteRecordRaw } from 'vue-router';

import { describe, expect, it } from 'vitest';

import assessRoutes from '#/router/routes/modules/assess';
import configRoutes from '#/router/routes/modules/config';
import diagnosisRoutes from '#/router/routes/modules/diagnosis';
import monitorRoutes from '#/router/routes/modules/monitor';
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

  it('/config/link（原 /loop/aas-sync）收紧为仅 ADMIN', () => {
    const route = findRoute(configRoutes, (r) => r.path === '/config/link');
    expect(route).toBeDefined();
    expect(authorityOf(route!)).toEqual(['ADMIN']);
  });

  it('监控-系统概览（/dashboard/workbench）排除 EXPERT', () => {
    const workbench = findRoute(
      monitorRoutes,
      (r) => r.path === '/dashboard/workbench',
    );
    expect(workbench).toBeDefined();
    expect(authorityOf(workbench!)).not.toContain('EXPERT');
    expect(authorityOf(workbench!)).toContain('SPONSOR');
  });

  it('评估（/assess）所有可见子路由排除 EXPERT、放行 SPONSOR', () => {
    const parent = assessRoutes[0]!;
    expect(authorityOf(parent)).not.toContain('EXPERT');
    for (const child of parent.children ?? []) {
      const auth = authorityOf(child);
      expect(auth.length).toBeGreaterThan(0);
      expect(auth).not.toContain('EXPERT');
      // 评估任务本就只对 ADMIN/IC_ENGINEER 开放，无需校验 SPONSOR
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

  it('诊断对 EXPERT 放行', () => {
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

  it('整定对 EXPERT 放行', () => {
    const parent = tuningRoutes[0]!;
    expect(authorityOf(parent)).toContain('EXPERT');
    for (const child of parent.children ?? []) {
      expect(authorityOf(child)).toContain('EXPERT');
    }
  });
});
