import { useEffect, useState, type FormEvent } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { Field } from '@/components/form';
import {
  getSetupGuide,
  removeProviderClient,
  saveProviderClient,
  testProviderClient,
  type CheckResult,
  type Provider,
  type ProviderSetup,
} from '@/lib/apps';
import { Group, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass } from '@/components/settings/ui';
import { CheckList } from './CheckList';

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="mt-2 rounded-[10px] border border-line bg-bg">
      <div className="flex items-center justify-between gap-2 border-b border-line px-2.5 py-1">
        <span className="font-mono text-[10.5px] tracking-[0.1em] text-fg-dim">{label.toUpperCase()}</span>
        <button type="button" onClick={() => void copy()} className={quietButtonClass} aria-label={`Copy ${label.toLowerCase()}`}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all px-2.5 py-2 font-mono text-[11.5px] leading-relaxed">{text}</pre>
    </div>
  );
}

function ProviderGuide({ setup, onChanged }: { setup: ProviderSetup; onChanged: (next: ProviderSetup) => void }) {
  const [clientId, setClientId] = useState(setup.client_id);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState<'save' | 'check' | 'remove' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);

  const act = async (kind: 'save' | 'check' | 'remove', fn: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e, 'Something went wrong. Please try again.'));
    } finally {
      setBusy(null);
    }
  };

  const check = () => act('check', async () => setResult(await testProviderClient(setup.id)));

  const save = (e: FormEvent) => {
    e.preventDefault();
    void act('save', async () => {
      onChanged(await saveProviderClient(setup.id, clientId.trim(), secret.trim()));
      setSecret('');
      setResult(await testProviderClient(setup.id));
    });
  };

  const remove = () =>
    act('remove', async () => {
      onChanged(await removeProviderClient(setup.id));
      setClientId('');
      setResult(null);
    });

  const ready = setup.source !== 'none';

  return (
    <div className="space-y-3 px-3.5 py-3">
      {setup.redirect_problem && <p role="alert" className="text-[12px] text-danger-ink">{setup.redirect_problem}</p>}
      <ol className="space-y-3">
        {setup.steps.map((step, i) => (
          <li key={step.title} className="flex gap-2.5">
            <span aria-hidden="true" className="grid h-5 w-5 flex-none place-items-center rounded-full border border-line font-mono text-[10.5px] text-fg-dim">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium">{step.title}</div>
              <p className="mt-0.5 text-[12px] leading-relaxed text-fg-dim">{step.body}</p>
              {step.link && (
                <a href={step.link.url} target="_blank" rel="noreferrer noopener" className="mt-1 inline-block text-[12px] text-accent-ink underline underline-offset-2">
                  {step.link.label}
                </a>
              )}
              {step.copy && <CopyBlock label={step.copy.label} text={step.copy.text} />}
            </div>
          </li>
        ))}
      </ol>

      {setup.can_edit ? (
        <form onSubmit={save} aria-label={`${setup.label} client`} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="CLIENT ID" required>
            <Input autoComplete="off" value={clientId} onChange={(e) => setClientId(e.target.value)} />
          </Field>
          <Field label="CLIENT SECRET" hint={ready ? 'Saved. Paste a new one to replace it.' : undefined} required>
            <Input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} />
          </Field>
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <button type="submit" disabled={busy !== null || !clientId.trim() || !secret.trim()} className={primaryButtonClass}>
              {busy === 'save' ? 'Saving…' : 'Save'}
            </button>
            <button type="button" disabled={busy !== null || !ready} onClick={() => void check()} className={buttonClass}>
              {busy === 'check' ? 'Checking…' : 'Check'}
            </button>
            {ready && (
              <button type="button" disabled={busy !== null} onClick={() => void remove()} className={quietButtonClass}>
                Remove
              </button>
            )}
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 flex-1 basis-[220px] text-[12px] leading-relaxed text-fg-dim">
            The client ID and secret come from the server's environment (<span className="font-mono">{setup.env_vars.join(', ')}</span>). Change them there.
          </p>
          <button type="button" disabled={busy !== null} onClick={() => void check()} className={buttonClass}>
            {busy === 'check' ? 'Checking…' : 'Check'}
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-[12px] text-danger-ink">{error}</p>}
      {result && <CheckList result={result} label={`${setup.label} check`} />}
    </div>
  );
}

/** Admin-only: the steps to register this server with Google, GitHub and
 *  Slack, with everything to paste filled in, and a check that says what is
 *  still wrong. */
export function AppSetupGuide({
  open,
  onOpen,
  onChanged,
}: {
  open: Provider | null;
  onOpen: (provider: Provider | null) => void;
  onChanged: () => void | Promise<void>;
}) {
  const [providers, setProviders] = useState<ProviderSetup[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getSetupGuide()
      .then(setProviders)
      .catch((e) => setLoadError(errorMessage(e, 'Could not load the setup steps.')));
  }, []);

  if (loadError) return <p role="alert" className="text-[12.5px] text-danger-ink">{loadError}</p>;
  if (!providers) return null;

  const replace = (next: ProviderSetup) => {
    setProviders((cur) => (cur || []).map((p) => (p.id === next.id ? next : p)));
    void onChanged();
  };

  return (
    <section id="app-setup" aria-label="Set up sign-in">
      <SubHeading>SET UP SIGN-IN · ADMIN</SubHeading>
      <p className="-mt-1 mb-2 max-w-[70ch] text-[12px] leading-relaxed text-fg-dim">
        Google, GitHub and Slack each need an app registered once for this server. Follow the steps, paste the client ID and
        secret, and press Check. Other apps need none of this.
      </p>
      <Group>
        {providers.map((setup) => {
          const isOpen = open === setup.id;
          return (
            <div key={setup.id}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3">
                <div className="min-w-0 flex-1 basis-[180px]">
                  <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
                    {setup.label}
                    {setup.source === 'none' ? <StatusPill tone="off">not set up</StatusPill> : <StatusPill tone="ok">ready</StatusPill>}
                  </div>
                  <div className="mt-0.5 text-[12px] leading-snug text-fg-dim">For {setup.apps.join(', ')}</div>
                </div>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => onOpen(isOpen ? null : setup.id)}
                  className={setup.source === 'none' ? buttonClass : quietButtonClass}
                >
                  {isOpen ? 'Hide steps' : setup.source === 'none' ? `Set up ${setup.label}` : 'Show steps'}
                </button>
              </div>
              {isOpen && <div className="border-t border-line"><ProviderGuide setup={setup} onChanged={replace} /></div>}
            </div>
          );
        })}
      </Group>
    </section>
  );
}
