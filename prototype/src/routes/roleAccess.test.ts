import { describe, expect, it } from 'vitest';
import { menuConfig } from './menuConfig';
import { canAccessPath, filterMenuByRole, getDefaultRouteForRole } from './roleAccess';
import { findRoute, getRouteConfig } from './routeConfig';

describe('menuConfig metadata', () => {
  it('declares parentId, pageLevel, stage and roles for every child route', () => {
    const childRoutes = menuConfig.flatMap((group) => group.children ?? []);

    expect(childRoutes.length).toBeGreaterThan(0);

    childRoutes.forEach((item) => {
      expect(item.parentId).toBeTruthy();
      expect(item.pageLevel).toBeTruthy();
      expect(item.stage).toBeTruthy();
      expect(item.roles?.length).toBeGreaterThan(0);
    });
  });
});

describe('role-aware navigation access', () => {
  it('hides system routes from engineer role', () => {
    const engineerMenu = filterMenuByRole('engineer');

    expect(engineerMenu.some((group) => group.id === 'system')).toBe(false);
    expect(getRouteConfig('engineer').some((route) => route.path === '/system/safety')).toBe(false);
    expect(canAccessPath('engineer', '/system/safety')).toBe(false);
  });

  it('keeps system routes available for admin role', () => {
    const adminMenu = filterMenuByRole('admin');

    expect(adminMenu.some((group) => group.id === 'system')).toBe(true);
    expect(getRouteConfig('admin').some((route) => route.path === '/system/safety')).toBe(true);
    expect(canAccessPath('admin', '/system/safety')).toBe(true);
  });

  it('returns role-specific default entries', () => {
    expect(getDefaultRouteForRole('engineer')).toBe('/');
    expect(getDefaultRouteForRole('reviewer')).toBe('/closure/review');
    expect(getDefaultRouteForRole('implementer')).toBe('/closure/implementation');
    expect(getDefaultRouteForRole('sponsor')).toBe('/sponsor');
    expect(getDefaultRouteForRole('admin')).toBe('/system/safety');
  });

  it('resolves route lookup from role-specific route config', () => {
    expect(findRoute('admin', '/system/safety')?.id).toBe('safety');
    expect(findRoute('engineer', '/system/safety')).toBeUndefined();
    expect(findRoute('engineer', '/diagnosis/loop/TIC-1115')?.id).toBe('loop-evidence');
  });
});
