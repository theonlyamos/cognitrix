import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { errorMessage } from '@/lib/api';
import { listComputerApps, setComputerApp, type ComputerApp, type ComputerAppLevel } from '@/lib/computerApps';
import { Segmented, buttonClass } from '@/components/settings/ui';

const LEVELS: { value: ComputerAppLevel; label: string }[] = [
  { value: 'allow', label: 'Always allow' },
  { value: 'ask', label: 'Ask' },
  { value: 'deny', label: 'Never' },
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
      setError(errorMessage(err, 'Could not load apps.'));
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
      setError(errorMessage(err, 'Could not save that.'));
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
        Agents can click and type in an app&apos;s window in the background while you keep working. They ask first,
        unless you&apos;ve answered for that app here. Anything that needs to take over your screen always asks.
        {!available && ' Background control isn\'t available on the computer Cognitrix is running on.'}
      </p>
      {apps === null ? (
        <p className="mt-3 text-[12.5px] text-fg-dim">Loading…</p>
      ) : apps.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-fg-dim">No apps yet. Agents ask each time.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {apps.map((row) => (
            <li key={row.app} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
              <div className="min-w-0 flex-1 basis-[160px]">
                <div className="text-[13.5px] font-medium">{row.label}</div>
                <div className="break-all font-mono text-[11.5px] text-fg-dim">{row.app}</div>
              </div>
              <Segmented label={`Agents in ${row.label}`} value={row.level} options={LEVELS} onChange={(level) => { if (!busy) void save(row.app, level); }} />
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="notepad.exe"
          aria-label="Program name"
          className="h-9 min-w-0 flex-1 basis-[180px] rounded-lg border border-line bg-transparent px-2.5 font-mono text-[12.5px] outline-none focus:border-accent md:h-8"
        />
        <button type="submit" disabled={busy || !name.trim()} className={buttonClass}>Always allow</button>
      </form>
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}
