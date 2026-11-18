import { create } from 'zustand';

type Theme = 'light' | 'dark';

function initialTheme(): Theme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

export interface Toast {
  id: number;
  kind: 'success' | 'error' | 'info' | 'warning';
  title: string;
  body?: string;
}

interface UiState {
  theme: Theme;
  sidebarCollapsed: boolean;
  mobileNavOpen: boolean;
  toasts: Toast[];
  toggleTheme: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  setMobileNav: (v: boolean) => void;
  toast: (t: Omit<Toast, 'id'>) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useUi = create<UiState>((set, get) => ({
  theme: initialTheme(),
  sidebarCollapsed: (() => {
    try { return localStorage.getItem('gbp-sidebar') === '1'; } catch { return false; }
  })(),
  mobileNavOpen: false,
  toasts: [],
  toggleTheme: () => {
    const theme: Theme = get().theme === 'dark' ? 'light' : 'dark';
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try { localStorage.setItem('gbp-theme', theme); } catch { /* ignore */ }
    set({ theme });
  },
  setSidebarCollapsed: (v) => {
    try { localStorage.setItem('gbp-sidebar', v ? '1' : '0'); } catch { /* ignore */ }
    set({ sidebarCollapsed: v });
  },
  setMobileNav: (v) => set({ mobileNavOpen: v }),
  toast: (t) => {
    const id = nextId++;
    set({ toasts: [...get().toasts, { ...t, id }] });
    window.setTimeout(() => get().dismiss(id), t.kind === 'error' ? 7000 : 4000);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((x) => x.id !== id) }),
}));

export const toast = (t: Omit<Toast, 'id'>) => useUi.getState().toast(t);
