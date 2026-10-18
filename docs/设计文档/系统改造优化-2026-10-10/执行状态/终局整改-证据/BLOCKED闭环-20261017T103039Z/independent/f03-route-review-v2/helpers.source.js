function applicationRoute(url) {
    if (!url.hash)
        return url;
    const path = url.hash.slice(1).split(/[?#]/, 1)[0];
    if (!url.hash.startsWith('#/') || path.startsWith('//') || url.hash.includes('\\')) {
        throw new Error('Invalid application hash route');
    }
    const route = new URL(url.hash.slice(1), url.origin);
    if (route.origin !== url.origin || route.pathname !== path) {
        throw new Error('Application hash route must preserve its local path');
    }
    return route;
}
function applicationRouteUrl(current, path) {
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) {
        throw new Error('Application target must be a local absolute path');
    }
    const route = new URL(path, current.origin);
    if (route.origin !== current.origin || route.hash ||
        route.pathname !== path.split(/[?#]/, 1)[0] ||
        !['/config/loop', '/loop/workbench360', '/metric/loop-evaluation'].includes(route.pathname)) {
        throw new Error('Unexpected critical E2E target');
    }
    if (!current.hash)
        return route.toString();
    applicationRoute(current);
    const target = new URL(current);
    target.hash = `${route.pathname}${route.search}${route.hash}`;
    return target.toString();
}
function isAuthenticatedLanding(url, origin) {
    try {
        const route = applicationRoute(url);
        return url.origin === origin && route.origin === origin &&
            (route.pathname === '/cockpit' || route.pathname.startsWith('/cockpit/'));
    }
    catch {
        return false;
    }
}
