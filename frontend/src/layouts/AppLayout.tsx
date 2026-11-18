import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity, Bell, Building2, ChevronsLeft, ChevronsRight, FileSpreadsheet, FileText, GitCompareArrows,
  LayoutDashboard, Landmark, LineChart, LogOut, Menu, Moon, ScrollText, Settings, Sun, Wallet, Workflow, X, ArrowLeftRight,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { authApi, notificationsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import { useUi } from '@/stores/ui';

export const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, perm: 'dashboard:read', end: true },
  { to: '/accounts', label: 'Bank Accounts', icon: Wallet, perm: 'accounts:read' },
  { to: '/institutions', label: 'Institutions', icon: Landmark, perm: 'accounts:read' },
  { to: '/automation', label: 'Automation Center', icon: Workflow, perm: 'automation:read' },
  { to: '/statements', label: 'Statements', icon: FileText, perm: 'statements:read' },
  { to: '/transactions', label: 'Transactions', icon: ArrowLeftRight, perm: 'transactions:read' },
  { to: '/balances', label: 'Balances', icon: Building2, perm: 'accounts:read' },
  { to: '/reconciliation', label: 'Reconciliation', icon: GitCompareArrows, perm: 'reconciliation:read' },
  { to: '/analytics', label: 'Analytics', icon: LineChart, perm: 'dashboard:read' },
  { to: '/reports', label: 'Reports', icon: FileSpreadsheet, perm: 'reports:read' },
  { to: '/notifications', label: 'Notifications', icon: Bell, perm: 'notifications:read' },
  { to: '/audit-logs', label: 'Audit Logs', icon: ScrollText, perm: 'audit:read' },
  { to: '/settings', label: 'Settings', icon: Settings, perm: 'settings:read' },
];

function Brand({ collapsed }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <div className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-700 text-white shadow-sm">
        <Activity className="h-4 w-4" />
        <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-olive-500 ring-2 ring-surface" />
      </div>
      {!collapsed && (
        <div className="leading-tight min-w-0">
          <div className="text-[15px] font-semibold tracking-tight text-ink">Meridian</div>
          <div className="text-[11px] text-muted">Banking Intelligence</div>
        </div>
      )}
    </div>
  );
}

function NavItems({ collapsed, onNavigate, unread }: { collapsed?: boolean; onNavigate?: () => void; unread: number }) {
  const can = useAuth((s) => s.can);
  return (
    <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2" aria-label="Main navigation">
      {NAV.filter((n) => can(n.perm)).map((n) => (
        <NavLink key={n.to} to={n.to} end={n.end} onClick={onNavigate} title={collapsed ? n.label : undefined}
          className={({ isActive }) => clsx('group relative flex items-center gap-3 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition-colors',
            isActive ? 'bg-brand-50 text-brand-800 dark:bg-brand-900/40 dark:text-brand-100'
              : 'text-muted hover:bg-surface-2 hover:text-ink', collapsed && 'justify-center')}>
          {({ isActive }) => (
            <>
              {isActive && <motion.span layoutId="nav-active" className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-brand-600 dark:bg-brand-300" />}
              <n.icon className="h-[18px] w-[18px] shrink-0" aria-hidden />
              {!collapsed && <span className="truncate">{n.label}</span>}
              {n.to === '/notifications' && unread > 0 && (
                <span className={clsx('rounded-full bg-accent-500 text-white text-[10.5px] font-semibold num px-1.5 min-w-[18px] text-center',
                  collapsed ? 'absolute top-1 right-1' : 'ml-auto')}>{unread > 99 ? '99+' : unread}</span>
              )}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppLayout() {
  const { sidebarCollapsed, setSidebarCollapsed, mobileNavOpen, setMobileNav, theme, toggleTheme } = useUi();
  const user = useAuth((s) => s.user);
  const clear = useAuth((s) => s.clear);
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const [menu, setMenu] = useState(false);
  const { data: unread } = useQuery({ queryKey: ['notifications', 'unread'], queryFn: notificationsApi.unread,
    refetchInterval: 30_000 });

  useEffect(() => { setMobileNav(false); setMenu(false); }, [location.pathname, setMobileNav]);

  const signOut = async () => {
    try { await authApi.logout(); } catch { /* session may already be gone */ }
    clear();
    qc.clear();
    navigate('/login', { replace: true });
  };

  const initials = (user?.full_name ?? '?').split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase();
  const count = unread?.unread ?? 0;

  return (
    <div className="min-h-screen flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70] focus:rounded focus:bg-surface focus:px-3 focus:py-2">Skip to content</a>
      {/* Desktop sidebar */}
      <aside className={clsx('hidden lg:flex fixed inset-y-0 left-0 z-30 flex-col border-r border-line bg-surface transition-[width] duration-200',
        sidebarCollapsed ? 'w-[72px]' : 'w-64')}>
        <div className={clsx('flex h-16 items-center border-b border-line px-4', sidebarCollapsed && 'justify-center px-2')}>
          <Brand collapsed={sidebarCollapsed} />
        </div>
        <NavItems collapsed={sidebarCollapsed} unread={count} />
        <div className="border-t border-line p-3">
          <button onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="flex w-full items-center justify-center gap-2 rounded-lg px-2 py-2 text-[13px] text-muted hover:bg-surface-2 hover:text-ink"
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            {sidebarCollapsed ? <ChevronsRight className="h-4 w-4" /> : <><ChevronsLeft className="h-4 w-4" />Collapse</>}
          </button>
        </div>
      </aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileNavOpen && (
          <div className="lg:hidden fixed inset-0 z-40">
            <motion.div className="absolute inset-0 bg-slate-950/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              exit={{ opacity: 0 }} onClick={() => setMobileNav(false)} />
            <motion.aside className="absolute inset-y-0 left-0 w-[min(18rem,85vw)] bg-surface border-r border-line flex flex-col"
              initial={{ x: '-100%' }} animate={{ x: 0 }} exit={{ x: '-100%' }} transition={{ type: 'spring', stiffness: 380, damping: 36 }}>
              <div className="flex h-16 items-center justify-between border-b border-line px-4">
                <Brand />
                <button onClick={() => setMobileNav(false)} aria-label="Close navigation" className="p-2 text-muted hover:text-ink"><X className="h-5 w-5" /></button>
              </div>
              <NavItems unread={count} onNavigate={() => setMobileNav(false)} />
            </motion.aside>
          </div>
        )}
      </AnimatePresence>

      <div className={clsx('flex-1 min-w-0 flex flex-col transition-[padding] duration-200', sidebarCollapsed ? 'lg:pl-[72px]' : 'lg:pl-64')}>
        <header className="sticky top-0 z-20 h-16 border-b border-line bg-surface/85 backdrop-blur supports-[backdrop-filter]:bg-surface/70">
          <div className="flex h-full items-center gap-2 px-4 sm:px-6">
            <button className="lg:hidden -ml-1 p-2 text-muted hover:text-ink" onClick={() => setMobileNav(true)} aria-label="Open navigation">
              <Menu className="h-5 w-5" />
            </button>
            <div className="lg:hidden"><Brand collapsed /></div>
            <div className="flex-1" />
            <button onClick={toggleTheme} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink"
              aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              {theme === 'dark' ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
            </button>
            <button onClick={() => navigate('/notifications')} className="relative rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink"
              aria-label={`Notifications${count ? `, ${count} unread` : ''}`}>
              <Bell className="h-[18px] w-[18px]" />
              {count > 0 && <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-accent-500 ring-2 ring-surface" />}
            </button>
            <div className="relative">
              <button onClick={() => setMenu((v) => !v)} aria-haspopup="menu" aria-expanded={menu}
                className="flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-2 hover:bg-surface-2">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-700 text-[12px] font-semibold text-white">{initials}</span>
                <span className="hidden sm:block text-left leading-tight">
                  <span className="block text-[13px] font-medium text-ink max-w-[160px] truncate">{user?.full_name}</span>
                  <span className="block text-[11px] text-muted">{user?.role_name}</span>
                </span>
              </button>
              <AnimatePresence>
                {menu && (
                  <motion.div role="menu" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                    className="absolute right-0 mt-2 w-60 card shadow-pop p-1.5 z-50">
                    <div className="px-3 py-2 border-b border-line mb-1">
                      <p className="text-sm font-medium text-ink truncate">{user?.full_name}</p>
                      <p className="text-[12px] text-muted truncate">{user?.email}</p>
                    </div>
                    <button role="menuitem" onClick={() => navigate('/settings')} className="w-full flex items-center gap-2 rounded-md px-3 py-2 text-sm text-ink hover:bg-surface-2">
                      <Settings className="h-4 w-4 text-muted" />Settings</button>
                    <button role="menuitem" onClick={signOut} className="w-full flex items-center gap-2 rounded-md px-3 py-2 text-sm text-ink hover:bg-surface-2">
                      <LogOut className="h-4 w-4 text-muted" />Sign out</button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </header>
        <main id="main" className="flex-1 px-4 sm:px-6 lg:px-8 py-6 lg:py-8 max-w-[1600px] w-full mx-auto">
          <motion.div key={location.pathname} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
            <Outlet />
          </motion.div>
        </main>
      </div>
    </div>
  );
}
