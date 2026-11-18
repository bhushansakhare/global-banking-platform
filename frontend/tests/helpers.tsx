import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { useAuth } from '@/stores/auth';
import type { User } from '@/types/api';

export const ALL_PERMS = ['dashboard:read', 'accounts:read', 'accounts:write', 'connections:manage', 'statements:read',
  'statements:write', 'transactions:read', 'transactions:write', 'reconciliation:read', 'reconciliation:write',
  'automation:read', 'automation:manage', 'reports:read', 'reports:write', 'notifications:read', 'audit:read',
  'settings:read', 'settings:manage', 'users:manage'];

export function makeUser(perms: string[] = ALL_PERMS, role: User['role'] = 'owner'): User {
  return { id: 'u1', email: 'owner@example.com', full_name: 'Olivia Owner', role, role_name: role, is_active: true,
    last_login_at: null, created_at: '2026-01-01T00:00:00Z', permissions: perms };
}

export function signIn(perms?: string[], role?: User['role']) {
  useAuth.getState().setSession('token-abc', 'csrf-xyz', makeUser(perms, role));
}

export function renderWithProviders(ui: ReactElement, { route = '/' }: { route?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter></QueryClientProvider>,
  );
}

type Handler = (url: string, init?: RequestInit) => unknown | Promise<unknown>;

/** Route-based fetch mock: keys are path prefixes after /api/v1. */
export function mockFetch(routes: Record<string, Handler | object>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^.*\/api\/v1/, '').split('?')[0];
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => path.startsWith(k));
    if (!key) return new Response(JSON.stringify({ error: { code: 'not_found', message: 'no mock', details: null, request_id: null } }), { status: 404 });
    const h = routes[key];
    const body = typeof h === 'function' ? await (h as Handler)(url, init) : h;
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}
