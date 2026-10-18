function applicationRoute(url) {
    return url.hash.startsWith('#/') ? new URL(url.hash.slice(1), url.origin) : url;
}
function applicationRouteUrl(current, path) {
    const route = new URL(path, current.origin);
    if (!current.hash.startsWith('#/'))
        return route.toString();
    const target = new URL(current);
    target.hash = `${route.pathname}${route.search}${route.hash}`;
    return target.toString();
}
function isAuthenticatedLanding(url, origin) {
    const route = applicationRoute(url);
    return url.origin === origin &&
        (route.pathname === '/cockpit' || route.pathname.startsWith('/cockpit/'));
}
