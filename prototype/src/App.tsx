import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AppSessionProvider, useAppSession } from './app/session/AppSessionContext';
import { AppShell } from './components/AppShell';
import { GenericPage } from './pages/GenericPage';
import { NotFoundPage } from './pages/pageShared';
import { getRouteConfig, findRoute } from './routes/routeConfig';
import { canAccessPath } from './routes/roleAccess';
import { UnauthorizedState } from './components/UnauthorizedState';

function RoutedApp() {
  const location = useLocation();
  const { role, defaultRoute } = useAppSession();
  const visibleRoutes = getRouteConfig(role);
  const homeRoute = visibleRoutes.find((route) => route.path === '/');
  const loopEvidenceRoute = findRoute(role, '/diagnosis/loop/seed');
  const isUnauthorized = location.pathname !== '/' && !canAccessPath(role, location.pathname);

  if (isUnauthorized) {
    return (
      <AppShell>
        <UnauthorizedState />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <Routes>
        {visibleRoutes.map((route) => (
          <Route key={route.id} path={route.path} element={<GenericPage route={route} />} />
        ))}
        {loopEvidenceRoute ? <Route path="/diagnosis/loop/:loopId" element={<GenericPage route={loopEvidenceRoute} />} /> : null}
        {homeRoute ? null : <Route path="/" element={<Navigate to={defaultRoute} replace />} />}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppSessionProvider>
        <RoutedApp />
      </AppSessionProvider>
    </BrowserRouter>
  );
}
