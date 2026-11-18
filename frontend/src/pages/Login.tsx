import { Eye, EyeOff, Lock } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button, Field, Input } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { EMAIL_RE } from '@/features/auth/validation';
import { AuthLayout } from '@/layouts/AuthLayout';
import { authApi } from '@/services/api';
import { ApiError } from '@/services/http';
import { useAuth } from '@/stores/auth';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<{ email?: string; password?: string }>({});
  const [loading, setLoading] = useState(false);
  const setSession = useAuth((s) => s.setSession);
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? '/';

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const fe: typeof fieldErr = {};
    if (!EMAIL_RE.test(email)) fe.email = 'Enter a valid email address';
    if (!password) fe.password = 'Enter your password';
    setFieldErr(fe);
    if (Object.keys(fe).length) return;
    setLoading(true);
    setError(null);
    try {
      const r = await authApi.login(email, password);
      setSession(r.access_token, r.csrf_token, r.user);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unable to sign in. Check that the server is running.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout title="Sign in" subtitle="Welcome back. Sign in to your banking workspace.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Notice tone="danger">{error}</Notice>}
        <Field label="Email address" htmlFor="email" error={fieldErr.email}>
          <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
            aria-invalid={!!fieldErr.email} placeholder="you@company.com" />
        </Field>
        <Field label="Password" htmlFor="password" error={fieldErr.password}>
          <div className="relative">
            <Input id="password" type={show ? 'text' : 'password'} autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} aria-invalid={!!fieldErr.password} className="pr-10" />
            <button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? 'Hide password' : 'Show password'}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-muted hover:text-ink">
              {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </Field>
        <Button type="submit" className="w-full justify-center" size="lg" loading={loading} icon={<Lock className="h-4 w-4" />}>
          Sign in
        </Button>
        <p className="text-[12px] text-muted text-center">Accounts lock for 15 minutes after five failed attempts.</p>
      </form>
    </AuthLayout>
  );
}
