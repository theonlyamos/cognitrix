import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { errorMessage } from '@/lib/api';
import { listComputerApps, setComputerApp, type ComputerApp, type ComputerAppLevel } from '@/lib/computerApps';
import { Segmented, buttonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';

const LEVELS: { value: ComputerAppLevel; label: string }[] = [
  { value: 'allow', get label() { return tr('ui.computerAppsCard.alwaysAllow'); } },
  { value: 'ask', get label() { return tr('ui.computerAppsCard.ask'); } },
  { value: 'deny', get label() { return tr('ui.computerAppsCard.never'); } },
];

/** Settings › Agent access: apps agents may click and type in without asking. */
export function ComputerAppsCard() {
  const [apps, setApps] = useState<ComputerApp[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await listComputerApps();
      setApps(data.apps);
      setAvailable(data.available);
    } catch (err) {
      setApps([]);
      setError(errorMessage(err, tr('ui.computerAppsCard.couldNotLoadApps')));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = async (app: string, level: ComputerAppLevel) => {
    setBusy(true);
    setError('');
    try {
      setApps(await setComputerApp(app, level));
      return true;
    } catch (err) {
      setError(errorMessage(err, tr('ui.computerAppsCard.couldNotSaveThat')));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const app = name.trim();
    if (app && await save(app, 'allow')) setName('');
  };

  return (
    <div>
      <p className="text-[12.5px] leading-relaxed text-fg-dim">
        {tr('ui.computerAppsCard.agentsCanClickAndType', { value: !available && tr('ui.computerAppsCard.backgroundControlIsnTAvailable') })}
      </p>
      {apps === null ? (
        <p className="mt-3 text-[12.5px] text-fg-dim">{tr('ui.computerAppsCard.loading')}</p>
      ) : apps.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-fg-dim">{tr('ui.computerAppsCard.noAppsYetAgentsAsk')}</p>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {apps.map((row) => (
            <li key={row.app} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
              <div className="min-w-0 flex-1 basis-[160px]">
                <div className="text-[13.5px] font-medium">{row.label}</div>
                <div className="break-all font-mono text-[11.5px] text-fg-dim">{row.app}</div>
              </div>
              <Segmented label={tr('ui.computerAppsCard.agentsInLabel', { label: row.label })} value={row.level} options={LEVELS} onChange={(level) => { if (!busy) void save(row.app, level); }} />
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="notepad.exe"
          aria-label={tr('ui.computerAppsCard.programName')}
          className="h-9 min-w-0 flex-1 basis-[180px] rounded-lg border border-line bg-transparent px-2.5 font-mono text-[12.5px] outline-none focus:border-accent md:h-8"
        />
        <button type="submit" disabled={busy || !name.trim()} className={buttonClass}>{tr('ui.computerAppsCard.alwaysAllow')}</button>
      </form>
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}
