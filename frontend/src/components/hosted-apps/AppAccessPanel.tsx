import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { KeyRound, Lock, PauseCircle, Plug, RefreshCw, Trash2 } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Toggle } from '@/pages/Notifications';
import type { AppAccessInfo, AppDetail } from '@/lib/hostedApps';
import { tr } from '@/lib/i18n';

const heading = 'mb-1 font-mono text-[10px] tracking-[0.16em] text-fg-dim';
const small = 'inline-flex h-8 flex-none items-center gap-1.5 rounded-md border border-line bg-panel px-2.5 text-[12.5px] font-medium hover:bg-panel-2 disabled:opacity-50';
const field = 'h-8 min-w-0 rounded-md border border-line bg-bg px-2 text-[12.5px]';
const asked = 'ml-1.5 rounded bg-accent/20 px-1.5 py-0.5 font-mono text-[9.5px] tracking-wider text-accent-ink';

/**
 * The owner's Access tab: what the app's own server code may use with nobody
 * looking (cognitrix/hosted_apps/access.py). Connected apps and saved sign-ins
 * take effect at once; secrets reach the app the next time it starts.
 */
export function AppAccessPanel({ app, onChanged }: { app: AppDetail; onChanged: (d: AppDetail) => void }) {
  const [info, setInfo] = useState<AppAccessInfo | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const base = `/hosted-apps/${app.id}`;

  const load = useCallback(async () => {
    try {
      setInfo((await api.get<AppAccessInfo>(`${base}/access`)).data);
    } catch (e) {
      setError(errorMessage(e, tr('ui.hostedApps.appAccessPanel.couldNotLoadWhatThis')));
    }
  }, [base]);
  useEffect(() => { void load(); }, [load, app.version]);

  const act = async (what: string, work: () => Promise<AppAccessInfo | void>) => {
    setBusy(what);
    setError('');
    try {
      const out = await work();
      if (out) setInfo(out);
      return true;
    } catch (e) {
      setError(errorMessage(e, tr('ui.hostedApps.appAccessPanel.couldNotSaveThat')));
      return false;
    } finally {
      setBusy('');
    }
  };
  const put = (body: Record<string, unknown>) => act('access', async () => (await api.put<AppAccessInfo>(`${base}/access`, body)).data);
  const secrets = (body: Record<string, unknown>) => act('secrets', async () => (await api.put<AppAccessInfo>(`${base}/secrets`, body)).data);
  const restart = () => act('restart', async () => {
    onChanged((await api.post<AppDetail>(`${base}/redeploy`, { note: 'Restarted to pick up its access and secrets' })).data);
    return (await api.get<AppAccessInfo>(`${base}/access`)).data;
  });
  const addSecret = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !value) return;
    if (await secrets({ put: { [name.trim()]: value } })) {
      setName('');
      setValue('');
    }
  };

  if (!info) return error ? <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p> : <p className="text-[13px] text-fg-dim">{tr('ui.hostedApps.appAccessPanel.loading')}</p>;
  const allowed = info.credentials.filter((c) => c.allowed).map((c) => c.name);
  const missing = [...info.asked.apps, ...info.asked.credentials];

  return (
    <div className="flex flex-col gap-5">
      <p className="text-[12.5px] leading-snug text-fg-dim">
        {tr('ui.hostedApps.appAccessPanel.whatThisAppSOwn')}
      </p>

      {info.paused && (
        <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-[12.5px]">
          <p className="flex items-start gap-2 font-medium"><PauseCircle size={15} className="mt-0.5 flex-none" />{tr('ui.hostedApps.appAccessPanel.accessIsPaused')}</p>
          <p className="mt-1 text-fg-dim">{tr('ui.hostedApps.appAccessPanel.someoneElsePublishedTheVersion')}</p>
          <button type="button" className={`${small} mt-2`} disabled={busy !== ''} onClick={() => void put({ paused: false })}>{tr('ui.hostedApps.appAccessPanel.turnAccessBackOn')}</button>
        </div>
      )}

      {info.needs_restart && (
        <div className="rounded-md border border-line bg-bg p-3 text-[12.5px]">
          <p className="font-medium">{tr('ui.hostedApps.appAccessPanel.restartTheAppToApply')}</p>
          <p className="mt-1 text-fg-dim">{info.can_restart ? tr('ui.hostedApps.appAccessPanel.theRunningVersionStartedBefore') : tr('ui.hostedApps.appAccessPanel.theRunningVersionStartedBefore_4936')}</p>
          {info.can_restart && (
            <button type="button" className={`${small} mt-2`} disabled={busy !== ''} onClick={() => void restart()}>
              <RefreshCw size={13} />{busy === 'restart' ? tr('ui.hostedApps.appAccessPanel.restarting') : tr('ui.hostedApps.appAccessPanel.restartNow')}
            </button>
          )}
        </div>
      )}

      {missing.length > 0 && (
        <p className="rounded-md border border-line bg-bg p-3 text-[12.5px] text-fg-dim">
          {tr('ui.hostedApps.appAccessPanel.thisAppAlsoAskedFor_5d48', { missing: missing.join(', ') })}
        </p>
      )}

      <section>
        <div className={heading}>{tr('ui.hostedApps.appAccessPanel.connectedApps')}</div>
        {info.apps.length === 0 ? (
          <p className="text-[12.5px] text-fg-dim">{tr('ui.hostedApps.appAccessPanel.youHaveNoConnectedApps')}</p>
        ) : (
          <div className="divide-y divide-line">
            {info.apps.map((a) => (
              <div key={a.id} className="flex items-center gap-2.5 py-2">
                <Plug size={14} className="flex-none text-fg-dim" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-medium">{a.name}{a.asked && !a.level && <span className={asked}>{tr('ui.hostedApps.appAccessPanel.askedFor')}</span>}</span>
                  {a.account && <span className="block truncate text-[11.5px] text-fg-dim">{a.account}</span>}
                </span>
                <select aria-label={tr('ui.hostedApps.appAccessPanel.whatThisAppMayDo', { name: a.name })} value={a.level} disabled={busy !== ''}
                  onChange={(e) => void put({ apps: { [a.id]: e.target.value } })} className={field}>
                  <option value="">{tr('ui.hostedApps.appAccessPanel.off')}</option>
                  <option value="read">{tr('ui.hostedApps.appAccessPanel.readOnly')}</option>
                  <option value="all">{tr('ui.hostedApps.appAccessPanel.readAndChange')}</option>
                </select>
              </div>
            ))}
          </div>
        )}
        {info.apps.some((a) => a.level === 'all') && (
          <p className="mt-1 text-[11.5px] leading-snug text-fg-dim">{tr('ui.hostedApps.appAccessPanel.withReadAndChangeThe')}</p>
        )}
      </section>

      <section>
        <div className={heading}>{tr('ui.hostedApps.appAccessPanel.savedSignIns')}</div>
        {info.credentials.length === 0 ? (
          <p className="text-[12.5px] text-fg-dim">{tr('ui.hostedApps.appAccessPanel.youHaveNoSavedSign')}</p>
        ) : (
          <div className="divide-y divide-line">
            {info.credentials.map((c) => (
              <div key={c.name} className="flex items-start gap-2.5 py-2">
                <KeyRound size={14} className="mt-0.5 flex-none text-fg-dim" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[12px] font-medium">{c.name}{c.asked && !c.allowed && <span className={asked}>{tr('ui.hostedApps.appAccessPanel.askedFor')}</span>}</span>
                  <span className="block truncate text-[11.5px] text-fg-dim">{c.allowed_hosts.length ? tr('ui.hostedApps.appAccessPanel.onlySentToAllowedHosts', { allowed_hosts: c.allowed_hosts.join(', ') }) : tr('ui.hostedApps.appAccessPanel.noSitesSet')}</span>
                </span>
                <Toggle label={tr('ui.hostedApps.appAccessPanel.letThisAppUseName', { name: c.name })} checked={c.allowed}
                  onChange={(on) => void put({ credentials: on ? [...allowed, c.name] : allowed.filter((n) => n !== c.name) })} />
              </div>
            ))}
          </div>
        )}
        <p className="mt-1 text-[11.5px] leading-snug text-fg-dim">{tr('ui.hostedApps.appAccessPanel.theAppNeverSeesThese')}</p>
      </section>

      <section>
        <div className={heading}>{tr('ui.hostedApps.appAccessPanel.secretsForThisApp')}</div>
        <p className="mb-1 text-[11.5px] leading-snug text-fg-dim">{tr('ui.hostedApps.appAccessPanel.keysOnlyThisAppNeeds')}</p>
        {info.secrets.length > 0 && (
          <div className="divide-y divide-line">
            {info.secrets.map((s) => (
              <div key={s.name} className="flex items-center gap-2.5 py-2">
                <Lock size={14} className="flex-none text-fg-dim" />
                <span className="min-w-0 flex-1 truncate font-mono text-[12px] font-medium">{s.name}</span>
                <span className="flex-none font-mono text-[11px] text-fg-dim">{s.hint || '••••'}</span>
                <button type="button" aria-label={tr('ui.hostedApps.appAccessPanel.removeName', { name: s.name })} title={tr('ui.hostedApps.appAccessPanel.removeName', { name: s.name })} disabled={busy !== ''}
                  onClick={() => void secrets({ remove: [s.name] })}
                  className="grid h-8 w-8 flex-none place-items-center rounded-md text-fg-dim hover:bg-panel-2 hover:text-danger-ink disabled:opacity-50"><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        )}
        {info.asked.secrets.map((wanted) => (
          <button key={wanted} type="button" onClick={() => setName(wanted)}
            className="mt-1 flex w-full items-center gap-2 rounded-md border border-dashed border-line px-2.5 py-2 text-left text-[12.5px] hover:bg-panel-2">
            <Lock size={14} className="flex-none text-fg-dim" /><span className="min-w-0 flex-1 truncate font-mono text-[12px]">{wanted}</span><span className={asked}>{tr('ui.hostedApps.appAccessPanel.needsAValue')}</span>
          </button>
        ))}
        <form onSubmit={(e) => void addSecret(e)} className="mt-2 flex flex-wrap items-center gap-2">
          <input aria-label={tr('ui.hostedApps.appAccessPanel.secretName')} placeholder={tr('ui.hostedApps.appAccessPanel.name')} value={name} onChange={(e) => setName(e.target.value)} spellCheck={false} autoComplete="off"
            className={`${field} w-[40%] flex-1 font-mono`} />
          <input aria-label={tr('ui.hostedApps.appAccessPanel.secretValue')} placeholder={tr('ui.hostedApps.appAccessPanel.value')} type="password" value={value} onChange={(e) => setValue(e.target.value)} autoComplete="new-password"
            className={`${field} w-[40%] flex-1`} />
          <button type="submit" className={small} disabled={busy !== '' || !name.trim() || !value}>{busy === 'secrets' ? tr('ui.hostedApps.appAccessPanel.saving') : tr('ui.hostedApps.appAccessPanel.save')}</button>
        </form>
      </section>

      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}
