/** Client-side validation mirrors the server policy (the server remains the authority). */
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function passwordProblems(pw: string): string[] {
  const p: string[] = [];
  if (pw.length < 12) p.push('at least 12 characters');
  if (!/[a-z]/.test(pw)) p.push('a lower-case letter');
  if (!/[A-Z]/.test(pw)) p.push('an upper-case letter');
  if (!/\d/.test(pw)) p.push('a digit');
  return p;
}

export function passwordStrength(pw: string): 0 | 1 | 2 | 3 | 4 {
  let s = 0;
  if (pw.length >= 12) s++;
  if (pw.length >= 16) s++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) s++;
  return Math.min(4, s) as 0 | 1 | 2 | 3 | 4;
}

export interface SetupForm {
  organisation: string; full_name: string; email: string; password: string; confirm: string;
  reporting_currency: string; timezone: string; country_code: string;
}

export function validateSetup(f: SetupForm): Partial<Record<keyof SetupForm, string>> {
  const e: Partial<Record<keyof SetupForm, string>> = {};
  if (f.organisation.trim().length < 2) e.organisation = 'Enter your organisation name';
  if (f.full_name.trim().length < 2) e.full_name = 'Enter your full name';
  if (!EMAIL_RE.test(f.email)) e.email = 'Enter a valid email address';
  const pp = passwordProblems(f.password);
  if (pp.length) e.password = `Password needs ${pp.join(', ')}`;
  if (f.confirm !== f.password) e.confirm = 'Passwords do not match';
  if (!/^[A-Z]{3}$/.test(f.reporting_currency)) e.reporting_currency = 'Choose a reporting currency';
  return e;
}
