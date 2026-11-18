import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom lacks these browser APIs used by charts and theme detection
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (q: string) => ({ matches: false, media: q, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }),
});
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
