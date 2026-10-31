import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, API_BASE } from '@/lib/api';
import { useUser } from '@/context/AppContext';
import { desktop, isDesktop } from '@/lib/desktop';

type Provider = 'google' | 'github';

const LABELS: Record<Provider, string> = { google: 'Google', github: 'GitHub' };
const POLL_MS = 1500;
const POLL_LIMIT_MS = 10 * 60 * 1000;

function ProviderIcon({ provider }: { provider: Provider }) {
  if (provider === 'google') {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.2-2.1 3.5-5.1 3.5-8.8z" />
        <path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5H1.3v3.1A12 12 0 0 0 12 24z" />
        <path fill="#FBBC05" d="M5.3 14.3a7.2 7.2 0 0 1 0-4.6V6.6H1.3a12 12 0 0 0 0 10.8l4-3.1z" />
        <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.3 6.6l4 3.1c.9-2.9 3.6-4.9 6.7-4.9z" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .5a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.6-1.4-1.4-1.8-1.4-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.7 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .5z" />
    </svg>
  );
}

function pollKey(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** "Continue with Google / GitHub". Shows only the providers the server has set up. */
export default function OAuthButtons({ mode }: { mode: 'signin' | 'signup' }) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [waiting, setWaiting] = useState<Provider | null>(null);
  const [params] = useSearchParams();
  const [error, setError] = useState<string | null>(params.get('oauth_error'));
  const { checkAuth } = useUser();
  const navigate = useNavigate();
  const cancelled = useRef(false);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => api.get<Record<string, boolean>>('/auth/oauth/providers'))
      .then(({ data }) => {
        if (live) setProviders((Object.keys(LABELS) as Provider[]).filter((p) => data?.[p]));
      })
      .catch(() => { /* older server: no buttons */ });
    return () => {
      live = false;
      cancelled.current = true;
    };
  }, []);

  const finish = async (token: string) => {
    localStorage.setItem('token', token);
    await checkAuth();
    navigate('/home', { replace: true });
  };

  const start = async (provider: Provider) => {
    setError(null);
    const startUrl = `${API_BASE}/auth/oauth/${provider}/start`;
    if (!isDesktop()) {
      window.location.assign(startUrl);
      return;
    }
    // The desktop window can't host the provider's page: sign in in the
    // browser, then collect the result here.
    const poll = pollKey();
    cancelled.current = false;
    setWaiting(provider);
    try {
      await desktop.openExternal(`${new URL(startUrl, window.location.origin).href}?poll=${poll}`);
      const deadline = Date.now() + POLL_LIMIT_MS;
      while (!cancelled.current && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (cancelled.current) return;
        const { data } = await api.post('/auth/oauth/poll', { poll });
        if (data.status === 'done') return await finish(data.access_token);
        if (data.status === 'error') {
          setError(data.error);
          return;
        }
      }
      if (!cancelled.current) setError('Sign-in timed out. Please try again.');
    } catch {
      setError(`Couldn't sign in with ${LABELS[provider]}. Please try again.`);
    } finally {
      setWaiting(null);
    }
  };

  if (!providers.length && !error) return null;
  const verb = mode === 'signup' ? 'Sign up' : 'Continue';

  return (
    <div className="mt-8">
      {providers.length > 0 && (
        <div className="grid gap-2.5">
          {providers.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => void start(p)}
              disabled={waiting !== null}
              className="flex h-11 w-full items-center justify-center gap-2.5 rounded-md border border-line bg-panel text-[14px] font-medium transition-colors hover:bg-panel-2 disabled:opacity-60"
            >
              <ProviderIcon provider={p} />
              {waiting === p ? `Waiting for ${LABELS[p]}…` : `${verb} with ${LABELS[p]}`}
            </button>
          ))}
        </div>
      )}
      {waiting && (
        <p role="status" className="mt-2 text-[12.5px] text-fg-dim">
          Finish signing in in your browser, then come back here.{' '}
          <button type="button" className="text-accent-ink hover:underline" onClick={() => { cancelled.current = true; setWaiting(null); }}>
            Cancel
          </button>
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>
      )}
      {providers.length > 0 && (
        <div className="mt-6 flex items-center gap-3 font-mono text-[10.5px] tracking-[0.14em] text-fg-dim">
          <span className="h-px flex-1 bg-line" /> OR WITH EMAIL <span className="h-px flex-1 bg-line" />
        </div>
      )}
    </div>
  );
}
