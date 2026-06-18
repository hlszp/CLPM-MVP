import type { NavigationItem, UserRole } from '../types';
import { flatMenu, menuConfig } from './menuConfig';

export function filterMenuByRole(role: UserRole): NavigationItem[] {
  return menuConfig
    .map((group) => ({
      ...group,
      children: (group.children ?? []).filter((item) => !item.roles || item.roles.includes(role)),
    }))
    .filter((group) => (group.children ?? []).length > 0);
}

export function getDefaultRouteForRole(role: UserRole): string {
  const matches = flatMenu.filter((item) => item.defaultEntry && item.roles?.includes(role));
  return matches[matches.length - 1]?.path ?? '/';
}

export function canAccessPath(role: UserRole, pathname: string): boolean {
  const visibleRoutes = filterMenuByRole(role).flatMap((group) => group.children ?? []);

  if (pathname.startsWith('/diagnosis/loop/')) {
    return visibleRoutes.some((item) => item.id === 'loop-evidence');
  }

  return visibleRoutes.some((item) => item.path === pathname);
}
