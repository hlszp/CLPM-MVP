/**
 * 路由权限三方对齐单元测试（基准 = 实现契约 §5 + UI/UX §4.2）
 *
 * 覆盖 2026-07-28 权限收紧项（IA 重构 Phase A 路径同步）：
 * - /system/reports 仅 ADMIN（后端 reports.py 全端点仅 ADMIN）
 * - /config/link（原 /loop/aas-sync）仅 ADMIN（后端 datasource.py/dcs.py 写端点仅 ADMIN）
 * - EXPERT 不可见系统概览/评估，但可进入监控下回路工作台
 */
import type { RouteRecordRaw } from 'vue-router';

import { describe, expect, it } from 'vitest';

import alertRoutes from '#/router/routes/modules/alert';
import assessRoutes from '#/router/routes/modules/assess';
import configRoutes from '#/router/routes/modules/config';
import monitorRoutes from '#/router/routes/modules/monitor';
import systemRoutes from '#/router/routes/modules/system';

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

  it('监控承载回路工作台，且对 EXPERT 放行', () => {
    const workbench = findRoute(
      monitorRoutes,
      (r) => r.path === '/monitor/loop-workbench',
    );
    expect(workbench).toBeDefined();
    expect(authorityOf(workbench!)).toContain('EXPERT');
    expect(workbench?.meta?.fullPathKey).toBe(false);
  });

  it('关注队列（/monitor/attention）全部角色可访问（Sponsor 只读）', () => {
    const attention = findRoute(
      monitorRoutes,
      (r) => r.path === '/monitor/attention',
    );
    expect(attention).toBeDefined();
    const auth = authorityOf(attention!);
    // 五角色全部放行——Sponsor 可查看关注队列（只读，无 OPEN_WORKBENCH）
    for (const role of [
      'ADMIN',
      'IC_ENGINEER',
      'PE_ENGINEER',
      'SPONSOR',
      'EXPERT',
    ]) {
      expect(auth).toContain(role);
    }
  });

  it('预警结果在监控、预警规则在配置；旧预警路由只做兼容跳转', () => {
    const events = findRoute(
      monitorRoutes,
      (r) => r.path === '/monitor/alerts',
    );
    const legacyEvents = findRoute(
      alertRoutes,
      (r) => r.path === '/alert/events',
    );
    const legacyRules = findRoute(
      alertRoutes,
      (r) => r.path === '/alert/rules',
    );
    const rules = findRoute(
      configRoutes,
      (r) => r.path === '/config/alert-rules',
    );
    expect(events).toBeDefined();
    expect(rules).toBeDefined();
    expect(authorityOf(rules!)).toEqual(['ADMIN']);
    expect(legacyEvents?.redirect).toBe('/monitor/alerts');
    expect(legacyRules?.redirect).toBe('/config/alert-rules');
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
          '/metric/kpi-report',
          '/metric/loop-performance',
          '/metric/pid-dashboard',
        ].includes(child.path)
      ) {
        expect(auth).toContain('SPONSOR');
      }
    }
  });

});
