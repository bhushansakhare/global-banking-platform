import { describe, expect, it } from 'vitest';
import { passwordProblems, validateSetup } from '@/features/auth/validation';
import { bytes, date, money, pct, titleCase } from '@/utils/format';
import { statusTone } from '@/utils/status';

describe('format', () => {
  it('formats money in British English with currency', () => {
    expect(money('1234.5', 'GBP')).toBe('£1,234.50');
    expect(money(-50, 'EUR')).toBe('-€50.00');
    expect(money('100', 'USD', { sign: true })).toBe('+US$100.00');
    expect(money(null, 'GBP')).toBe('—');
    expect(money('not-a-number', 'GBP')).toBe('—');
  });
  it('formats dates day-month-year', () => {
    expect(date('2026-03-05')).toBe('05 Mar 2026');
    expect(date(null)).toBe('—');
  });
  it('formats percentages, bytes and labels', () => {
    expect(pct('0.9512')).toBe('95.1%');
    expect(bytes(2048)).toBe('2.0 KB');
    expect(titleCase('needs_review')).toBe('Needs review');
  });
});

describe('status mapping', () => {
  it('reserves danger for genuine errors only', () => {
    expect(statusTone('failed')[0]).toBe('danger');
    expect(statusTone('needs_review')[0]).toBe('warning');
    expect(statusTone('suggested')[0]).toBe('info');
    expect(statusTone('demo')[0]).toBe('demo');
    expect(statusTone('awaiting_user')[1]).toBe('Awaiting you');
  });
});

describe('form validation', () => {
  it('enforces the password policy', () => {
    expect(passwordProblems('short')).toContain('at least 12 characters');
    expect(passwordProblems('alllowercase123')).toContain('an upper-case letter');
    expect(passwordProblems('GoodPassword123')).toEqual([]);
  });
  it('validates the setup form', () => {
    const base = { organisation: 'Acme', full_name: 'Ann Lee', email: 'ann@example.com', password: 'GoodPassword123',
      confirm: 'GoodPassword123', reporting_currency: 'GBP', timezone: 'Europe/London', country_code: 'GB' };
    expect(validateSetup(base)).toEqual({});
    const e = validateSetup({ ...base, email: 'nope', confirm: 'x', organisation: '' });
    expect(Object.keys(e).sort()).toEqual(['confirm', 'email', 'organisation']);
  });
});
