import { readCsrfCookie, useAuth } from '@/stores/auth';
import type { ApiErrorBody, TokenResponse } from '@/types/api';

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: Query): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
  });
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

let refreshing: Promise<boolean> | null = null;

/** Rotate the refresh token (single-flight so parallel 401s trigger one refresh). */
export function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const csrf = useAuth.getState().csrfToken ?? readCsrfCookie();
      if (!csrf) return false;
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST', credentials: 'include', headers: { 'X-CSRF-Token': csrf },
        });
        if (!res.ok) return false;
        const body = (await res.json()) as TokenResponse;
        useAuth.getState().setSession(body.access_token, body.csrf_token, body.user);
        return true;
      } catch {
        return false;
      }
    })().finally(() => {
      refreshing = null; // concurrent callers already hold this promise
    });
  }
  return refreshing;
}

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as ApiErrorBody;
    return new ApiError(res.status, body.error.code, body.error.message, body.error.details);
  } catch {
    return new ApiError(res.status, 'http_error', res.statusText || 'Request failed');
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: Query;
  body?: unknown;
  form?: FormData;
  raw?: boolean;
  retry?: boolean;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, form, raw, retry = true } = opts;
  const headers: Record<string, string> = {};
  const token = useAuth.getState().accessToken;
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const csrf = useAuth.getState().csrfToken;
  if (csrf) headers['X-CSRF-Token'] = csrf;

  const res = await fetch(buildUrl(path, query), {
    method, headers, credentials: 'include',
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (res.status === 401 && !path.startsWith('/auth/')) {
    if (retry && (await refreshSession())) return request<T>(path, { ...opts, retry: false });
    useAuth.getState().clear();
  }
  if (!res.ok) throw await parseError(res);
  if (raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Authenticated file download that respects auth headers (no tokens in URLs). */
export async function download(path: string, query?: Query, fallbackName = 'download'): Promise<void> {
  const res = await request<Response>(path, { query, raw: true });
  const blob = await res.blob();
  const disp = res.headers.get('content-disposition') ?? '';
  const m = disp.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function fetchBlobUrl(path: string, query?: Query): Promise<{ url: string; type: string }> {
  const res = await request<Response>(path, { query, raw: true });
  const blob = await res.blob();
  return { url: URL.createObjectURL(blob), type: blob.type };
}
