import clsx from 'clsx';
import { ArrowRight } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Button, Field, Input, Select } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { passwordStrength, validateSetup, type SetupForm } from '@/features/auth/validation';
import { AuthLayout } from '@/layouts/AuthLayout';
import { authApi } from '@/services/api';
import { ApiError } from '@/services/http';
import { useAuth } from '@/stores/auth';

const CURRENCIES = ['GBP', 'EUR', 'USD', 'INR', 'AED', 'SGD', 'AUD', 'CAD', 'CHF', 'JPY', 'SAR', 'ZAR', 'BRL'];
const ZONES = ['Europe/London', 'Europe/Berlin', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'America/New_York',
  'America/Chicago', 'America/Los_Angeles', 'Australia/Sydney', 'Asia/Tokyo', 'UTC'];
const COUNTRIES: [string, string][] = [['GB', 'United Kingdom'], ['IN', 'India'], ['US', 'United States'],
  ['DE', 'Germany'], ['FR', 'France'], ['AE', 'United Arab Emirates'], ['SG', 'Singapore'], ['AU', 'Australia'],
  ['CA', 'Canada'], ['IE', 'Ireland'], ['NL', 'Netherlands']];

export default function Setup() {
  const [f, setF] = useState<SetupForm>({ organisation: '', full_name: '', email: '', password: '', confirm: '',
    reporting_currency: 'GBP', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/London',
    country_code: 'GB' });
  const [errors, setErrors] = useState<ReturnType<typeof validateSetup>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setSession = useAuth((s) => s.setSession);
  const set = (k: keyof SetupForm) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const strength = passwordStrength(f.password);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validateSetup(f);
    setErrors(v);
    if (Object.keys(v).length) return;
    setLoading(true);
    setError(null);
    try {
      const r = await authApi.setup({ organisation: f.organisation, full_name: f.full_name, email: f.email,
        password: f.password, reporting_currency: f.reporting_currency, timezone: f.timezone, country_code: f.country_code });
      setSession(r.access_token, r.csrf_token, r.user);
      window.location.assign('/');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Setup failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout title="Set up your workspace" subtitle="One-time setup. You will be the owner of this organisation.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Notice tone="danger">{error}</Notice>}
        <Field label="Organisation" htmlFor="org" error={errors.organisation}>
          <Input id="org" value={f.organisation} onChange={set('organisation')} placeholder="Acme Treasury Ltd" />
        </Field>
        <div className="grid grid-cols-1 xs:grid-cols-2 gap-4">
          <Field label="Your full name" htmlFor="name" error={errors.full_name}>
            <Input id="name" value={f.full_name} onChange={set('full_name')} autoComplete="name" />
          </Field>
          <Field label="Email" htmlFor="email" error={errors.email}>
            <Input id="email" type="email" value={f.email} onChange={set('email')} autoComplete="username" />
          </Field>
        </div>
        <Field label="Password" htmlFor="pw" error={errors.password} hint="12+ characters with upper- and lower-case letters and a digit">
          <Input id="pw" type="password" value={f.password} onChange={set('password')} autoComplete="new-password" />
          <div className="mt-2 flex gap-1" aria-hidden>
            {[1, 2, 3, 4].map((i) => (
              <span key={i} className={clsx('h-1 flex-1 rounded-full', i <= strength
                ? ['', 'bg-amber-500', 'bg-amber-400', 'bg-olive-500', 'bg-olive-600'][strength] : 'bg-line')} />
            ))}
          </div>
        </Field>
        <Field label="Confirm password" htmlFor="pw2" error={errors.confirm}>
          <Input id="pw2" type="password" value={f.confirm} onChange={set('confirm')} autoComplete="new-password" />
        </Field>
        <div className="grid grid-cols-1 xs:grid-cols-3 gap-4">
          <Field label="Reporting currency" htmlFor="ccy" error={errors.reporting_currency}>
            <Select id="ccy" value={f.reporting_currency} onChange={set('reporting_currency')}>
              {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
            </Select>
          </Field>
          <Field label="Country" htmlFor="cc">
            <Select id="cc" value={f.country_code} onChange={set('country_code')}>
              {COUNTRIES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
            </Select>
          </Field>
          <Field label="Time zone" htmlFor="tz">
            <Select id="tz" value={f.timezone} onChange={set('timezone')}>
              {[...new Set([f.timezone, ...ZONES])].map((z) => <option key={z}>{z}</option>)}
            </Select>
          </Field>
        </div>
        <Button type="submit" size="lg" className="w-full justify-center" loading={loading} icon={<ArrowRight className="h-4 w-4" />}>
          Create workspace
        </Button>
      </form>
    </AuthLayout>
  );
}
