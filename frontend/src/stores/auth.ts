import { create } from 'zustand';
import type { User } from '@/types/api';

/**
 * The access token lives only in memory (never localStorage) to limit XSS exposure.
 * The refresh token is an httpOnly cookie the browser manages; the CSRF token is
 * mirrored from its readable cookie.
 */
interface AuthState {
  accessToken: string | null;
  csrfToken: string | null;
  user: User | null;
  status: 'unknown' | 'authenticated' | 'anonymous';
  setSession: (token: string, csrf: string, user: User) => void;
  clear: () => void;
  can: (perm: string) => boolean;
}

export const useAuth = create<AuthState>((set, get) => ({
  accessToken: null,
  csrfToken: null,
  user: null,
  status: 'unknown',
  setSession: (accessToken, csrfToken, user) => set({ accessToken, csrfToken, user, status: 'authenticated' }),
  clear: () => set({ accessToken: null, csrfToken: null, user: null, status: 'anonymous' }),
  can: (perm) => !!get().user?.permissions.includes(perm),
}));

export function readCsrfCookie(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)gbp_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}
