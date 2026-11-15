import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { getSandboxEnv, saveSandboxEnv, type SandboxEnvVar } from '@/lib/sandbox';
import { tr } from '@/lib/i18n';

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;

/**
 * Settings › Computer: environment variables every command, test run and
 * coding agent in this person's sandbox gets. Values are saved sealed on the
 * server and never come back, only a masked preview.
 */
export function SandboxEnvCard() {
  const [vars, setVars] = useState<SandboxEnvVar[] | null>(null);
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [hosts, setHosts] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setVars(await getSandboxEnv());
    } catch (err) {
      setError(errorMessage(err, tr('ui.sandboxEnvCard.couldnTLoadTheVariables')));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function save(next: SandboxEnvVar[]) {
    setBusy(true);
    setError('');
    try {
      setVars(await saveSandboxEnv(next));
      return true;
    } catch (err) {
      setError(errorMessage(err, tr('ui.sandboxEnvCard.couldnTSaveTheVariables')));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const clean = name.trim();
    if (!NAME_RE.test(clean)) {
      setError(tr('ui.sandboxEnvCard.useLettersNumbersAndFor'));
      return;
    }
    const rest = (vars ?? []).filter((v) => v.name !== clean).map((v) => ({ name: v.name }));
    const sites = hosts.split(/[\s,]+/).map((h) => h.trim()).filter(Boolean);
    // Changing only the sites keeps the saved value.
    const keep = editing !== null && value === '';
    if (await save([...rest, { name: clean, value: keep ? undefined : value, hosts: sites }])) {
      setName('');
      setValue('');
      setHosts('');
      setEditing(null);
    }
  }

  const remove = (target: string) => void save((vars ?? []).filter((v) => v.name !== target).map((v) => ({ name: v.name })));

  return (
    <div className="space-y-2">
      <div>
        <p className="text-[13.5px] font-medium">{tr('ui.sandboxEnvCard.environmentVariables')}</p>
        <p className="text-[12.5px] text-fg-dim">
          {tr('ui.sandboxEnvCard.everyCommandTestRunAnd_e5d4')}{' '}<span className="font-medium">{tr('ui.sandboxEnvCard.onlySentTo')}</span>{tr('ui.sandboxEnvCard.theAgentThenGetsA')}
        </p>
      </div>
      {vars && vars.length > 0 && (
        <ul className="divide-y divide-line rounded-[10px] border border-line bg-bg">
          {vars.map((v) => (
            <li key={v.name} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <span className="font-mono text-[13px]">{v.name}</span>
              {v.hosts && v.hosts.length > 0 ? (
                <span className="text-[11.5px] text-fg-dim" title={tr('ui.sandboxEnvCard.theSandboxGetsAStand')}>
                  {tr('ui.sandboxEnvCard.hiddenOnlySentToHosts', { hosts: v.hosts.join(', ') })}
                </span>
              ) : (
                <span className="font-mono text-[11.5px] text-fg-dim">{v.preview}</span>
              )}
              <span className="flex-1" />
              <button type="button" onClick={() => { setEditing(v.name); setName(v.name); setValue(''); setHosts((v.hosts ?? []).join(', ')); setError(''); }}
                className="text-[12.5px] text-fg-dim hover:text-fg">
                {tr('ui.sandboxEnvCard.change')}
              </button>
              <button type="button" onClick={() => remove(v.name)} disabled={busy}
                className="text-[12.5px] text-danger-ink hover:underline disabled:opacity-60">
                {tr('ui.sandboxEnvCard.remove')}
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-center gap-2">
        <Input aria-label={tr('ui.sandboxEnvCard.variableName')} value={name} onChange={(e) => setName(e.target.value.toUpperCase().replace(/\s+/g, '_'))}
          placeholder={tr('ui.sandboxEnvCard.name')} className="h-9 w-44 font-mono text-[13px]" readOnly={editing !== null} />
        <Input aria-label={tr('ui.sandboxEnvCard.variableValue')} type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={editing ? tr('ui.sandboxEnvCard.newValueEmptyKeepsIt') : tr('ui.sandboxEnvCard.value')} className="h-9 min-w-[10rem] flex-1 font-mono text-[13px]" />
        <Input aria-label={tr('ui.sandboxEnvCard.onlySentTo')} value={hosts} onChange={(e) => setHosts(e.target.value)}
          placeholder={tr('ui.sandboxEnvCard.onlySentToOptionalE')} className="h-9 w-full font-mono text-[13px] sm:w-64" />
        <button type="submit" disabled={busy || !name.trim()}
          className="h-9 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
          {busy ? tr('ui.sandboxEnvCard.saving') : editing ? tr('ui.sandboxEnvCard.save') : tr('ui.sandboxEnvCard.add')}
        </button>
        {editing && (
          <button type="button" onClick={() => { setEditing(null); setName(''); setValue(''); setHosts(''); }}
            className="h-9 rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
            {tr('ui.sandboxEnvCard.cancel')}
          </button>
        )}
      </form>
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}
