import { beforeEach, describe, expect, it } from 'vitest';
import { ApiError, request } from '@/services/http';
import { useAuth } from '@/stores/auth';
import { makeUser, mockFetch } from './helpers';

describe('API client', () => {
  beforeEach(() => useAuth.getState().clear());

  it('sends the bearer token and CSRF header', async () => {
    useAuth.getState().setSession('tok-1', 'csrf-1', makeUser());
    const f = mockFetch({ '/accounts': [] });
    await request('/accounts');
    const init = f.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-1');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-1');
    expect(init.credentials).toBe('include');
  });

  it('refreshes once on 401 and retries the original request', async () => {
    useAuth.getState().setSession('expired', 'csrf-1', makeUser());
    let calls = 0;
    mockFetch({
      '/auth/refresh': { access_token: 'fresh', csrf_token: 'csrf-2', expires_in: 900, user: makeUser() },
      '/accounts': () => {
        calls += 1;
        return calls === 1 ? new Response(JSON.stringify({ error: { code: 'token_expired', message: 'expired', details: null, request_id: null } }), { status: 401 })
          : [{ id: 'a1' }];
      },
    });
    const out = await request<{ id: string }[]>('/accounts');
    expect(out).toEqual([{ id: 'a1' }]);
    expect(useAuth.getState().accessToken).toBe('fresh');
    expect(useAuth.getState().csrfToken).toBe('csrf-2');
  });

  it('clears the session when refresh fails', async () => {
    useAuth.getState().setSession('expired', 'csrf-1', makeUser());
    mockFetch({
      '/auth/refresh': () => new Response('{}', { status: 401 }),
      '/accounts': () => new Response(JSON.stringify({ error: { code: 'token_expired', message: 'x', details: null, request_id: null } }), { status: 401 }),
    });
    await expect(request('/accounts')).rejects.toBeInstanceOf(ApiError);
    expect(useAuth.getState().status).toBe('anonymous');
  });

  it('surfaces structured API errors', async () => {
    useAuth.getState().setSession('t', 'c', makeUser());
    mockFetch({ '/accounts': () => new Response(JSON.stringify({ error: { code: 'forbidden', message: 'No permission', details: null, request_id: 'r1' } }), { status: 403 }) });
    await expect(request('/accounts')).rejects.toMatchObject({ status: 403, code: 'forbidden', message: 'No permission' });
  });
});
