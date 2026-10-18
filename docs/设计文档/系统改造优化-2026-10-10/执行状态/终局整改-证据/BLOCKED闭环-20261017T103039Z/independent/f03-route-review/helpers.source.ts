function applicationRoute(url: URL): URL {
  return url.hash.startsWith('#/') ? new URL(url.hash.slice(1), url.origin) : url;
}

function applicationRouteUrl(current: URL, path: string): string {
  const route = new URL(path, current.origin);
  if (!current.hash.startsWith('#/')) return route.toString();
  const target = new URL(current);
  target.hash = `${route.pathname}${route.search}${route.hash}`;
  return target.toString();
}

function isAuthenticatedLanding(url: URL, origin: string): boolean {
  const route = applicationRoute(url);
  return url.origin === origin &&
    (route.pathname === '/cockpit' || route.pathname.startsWith('/cockpit/'));
}