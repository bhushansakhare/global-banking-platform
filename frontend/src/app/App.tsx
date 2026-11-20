import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Spinner } from '@/components/ui/primitives';
import { Toaster } from '@/components/ui/layout';
import { AppLayout } from '@/layouts/AppLayout';
import { authApi } from '@/services/api';
import { ApiError, refreshSession } from '@/services/http';
import { useAuth } from '@/stores/auth';

const Login = lazy(() => import('@/pages/Login'));
const Setup = lazy(() => import('@/pages/Setup'));
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Accounts = lazy(() => import('@/pages/Accounts'));
const AccountDetail = lazy(() => import('@/pages/AccountDetail'));
const Institutions = lazy(() => import('@/pages/Institutions'));
const Automation = lazy(() => import('@/pages/Automation'));
const Statements = lazy(() => import('@/pages/Statements'));
const StatementDetail = lazy(() => import('@/pages/StatementDetail'));
const Transactions = lazy(() => import('@/pages/Transactions'));
const Balances = lazy(() => import('@/pages/Balances'));
const Reconciliation = lazy(() => import('@/pages/Reconciliation'));
const ReconciliationDetail = lazy(() => import('@/pages/ReconciliationDetail'));
const Analytics = lazy(() => import('@/pages/Analytics'));
const Reports = lazy(() => import('@/pages/Reports'));
const Notifications = lazy(() => import('@/pages/Notifications'));
const AuditLogs = lazy(() => import('@/pages/AuditLogs'));
const SettingsPage = lazy(() => import('@/pages/Settings'));
const Finzola = lazy(() => import('@/pages/Finzola'));
const NotFound = lazy(() => import('@/pages/NotFound'));

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && [401, 403, 404, 422].includes(err.status)) && count < 2,
    },
  },
});

function FullScreenLoader() {
  return <div className="min-h-screen flex items-center justify-center"><Spinner className="h-7 w-7" /></div>;
}

function useBootstrap() {
  const status = useAuth((s) => s.status);
  const clear = useAuth((s) => s.clear);
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await authApi.setupStatus();
        if (cancelled) return;
        setSetupRequired(s.setup_required);
        if (s.setup_required) { clear(); return; }
        if (useAuth.getState().status === 'unknown' && !(await refreshSession())) clear();
      } catch {
        if (!cancelled) { setSetupRequired(false); clear(); }
      }
    })();
    return () => { cancelled = true; };
  }, [clear]);
  return { status, setupRequired };
}

function Protected({ children }: { children: ReactNode }) {
  const status = useAuth((s) => s.status);
  const location = useLocation();
  if (status === 'unknown') return <FullScreenLoader />;
  if (status !== 'authenticated') return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

function Routed() {
  const { status, setupRequired } = useBootstrap();
  if (setupRequired === null || (status === 'unknown' && !setupRequired)) return <FullScreenLoader />;
  return (
    <Suspense fallback={<FullScreenLoader />}>
      <Routes>
        <Route path="/setup" element={setupRequired ? <Setup /> : <Navigate to="/login" replace />} />
        <Route path="/login" element={setupRequired ? <Navigate to="/setup" replace />
          : status === 'authenticated' ? <Navigate to="/" replace /> : <Login />} />
        <Route element={setupRequired ? <Navigate to="/setup" replace /> : <Protected><AppLayout /></Protected>}>
          <Route index element={<Dashboard />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="accounts/:id" element={<AccountDetail />} />
          <Route path="institutions" element={<Institutions />} />
          <Route path="automation" element={<Automation />} />
          <Route path="statements" element={<Statements />} />
          <Route path="statements/:id" element={<StatementDetail />} />
          <Route path="transactions" element={<Transactions />} />
          <Route path="balances" element={<Balances />} />
          <Route path="reconciliation" element={<Reconciliation />} />
          <Route path="reconciliation/:id" element={<ReconciliationDetail />} />
          <Route path="integrations/finzola" element={<Finzola />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="reports" element={<Reports />} />
          <Route path="notifications" element={<Notifications />} />
          <Route path="audit-logs" element={<AuditLogs />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routed />
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
