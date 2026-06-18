import type { NavigationItem, UserRole } from '../types';
import { filterMenuByRole } from './roleAccess';

export function getRouteConfig(role: UserRole = 'engineer'): NavigationItem[] {
  return filterMenuByRole(role).flatMap((group) => group.children ?? []);
}

export const routeConfig: NavigationItem[] = getRouteConfig();

export function findRoute(pathname: string): NavigationItem | undefined;
export function findRoute(role: UserRole, pathname: string): NavigationItem | undefined;
export function findRoute(roleOrPathname: UserRole | string, pathname?: string): NavigationItem | undefined {
  const role = pathname ? (roleOrPathname as UserRole) : 'engineer';
  const targetPath = pathname ?? roleOrPathname;
  const routes = getRouteConfig(role);

  if (targetPath.startsWith('/diagnosis/loop/')) {
    return routes.find((route) => route.id === 'loop-evidence');
  }

  return routes.find((route) => route.path === targetPath);
}
