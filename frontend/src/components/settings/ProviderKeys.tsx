import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { Input } from '@/lib/components/ui/input';
import { KEY_URLS, PROVIDER_LABELS } from '@/lib/providers';
import {
  listProviderKeys, removeProviderKey, saveProviderKey, testProviderKey,
  type ProviderKey, type ProviderKeyList,
} from '@/lib/providerKeys';
import { Group, SettingRow, StatusPill, buttonClass, primaryButtonClass, quietButtonClass } from './ui';

const SHORT: Record<string, string> = {
  openrouter: 'OR', openai: 'AI', anthropic: 'A\\', google: 'G', groq: 'GQ', cerebras: 'CB', ollama: 'OL',
};

type Result = { ok: boolean; message: string };

function Status({ row }: { row: ProviderKey }) {
  switch (row.source) {
    case 'env': return <StatusPill tone="ok">FROM .ENV</StatusPill>;
    case 'saved': return <StatusPill tone="ok">SAVED</StatusPill>;
    case 'local': return <StatusPill tone="off">NO KEY NEEDED</StatusPill>;
    default: return <StatusPill tone="off">NOT SET</StatusPill>;
  }
}

function ProviderRow({ row, canEdit, onChange }: { row: ProviderKey; canEdit: boolean; onChange: (next: ProviderKey) => void }) {
  const label = PROVIDER_LABELS[row.id] ?? row.id;
  const [adding, setAdding] = useState(false);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | 'remove' | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const test = async () => {
    setBusy('test');
    setResult(null);
    try {
      setResult(await testProviderKey(row.id));
    } catch (e) {
      setResult({ ok: false, message: errorMessage(e, 'Could not run the test.') });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy('save');
    setResult(null);
    try {
      onChange(await saveProviderKey(row.id, key.trim()));
      setKey('');
      setAdding(false);
      setBusy('test');
      setResult(await testProviderKey(row.id));
    } catch (e) {
      setResult({ ok: false, message: errorMessage(e, 'Could not save the key.') });
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy('remove');
    setResult(null);
    try {
      onChange(await removeProviderKey(row.id));
    } catch (e) {
      setResult({ ok: false, message: errorMessage(e, 'Could not remove the key.') });
    } finally {
      setBusy(null);
    }
  };

  const detail = (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <Status row={row} />
      {row.source === 'env' && <span className="font-mono text-[11.5px]">{row.env_var}</span>}
      {row.source === 'saved' && row.hint && <span className="font-mono text-[11.5px]">{row.hint}</span>}
      {row.source === 'local' && <span className="font-mono text-[11.5px]">{row.base_url.replace(/^https?:\/\//, '').replace(/\/v1\/?$/, '')}</span>}
    </span>
  );

  const editable = canEdit && row.source !== 'env' && row.source !== 'local';

  return (
    <div>
      <SettingRow
        label={label}
        detail={detail}
        lead={<span aria-hidden="true" className="grid h-7 w-7 flex-none place-items-center rounded-[7px] bg-panel-2 font-mono text-[11px] font-medium">{SHORT[row.id] ?? row.id[0].toUpperCase()}</span>}
      >
        {row.source !== 'none' && (
          <button type="button" onClick={test} disabled={!!busy} className={quietButtonClass}>
            {busy === 'test' ? 'Testing…' : row.source === 'local' ? 'Check' : 'Test'}
          </button>
        )}
        {editable && row.source === 'saved' && !adding && (
          <>
            <button type="button" onClick={() => setAdding(true)} disabled={!!busy} className={quietButtonClass}>Replace</button>
            <button type="button" onClick={remove} disabled={!!busy} className={quietButtonClass}>
              {busy === 'remove' ? 'Removing…' : 'Remove'}
            </button>
          </>
        )}
        {editable && row.source === 'none' && !adding && (
          <button type="button" onClick={() => setAdding(true)} className={buttonClass}>Add key</button>
        )}
      </SettingRow>

      {adding && (
        <form
          className="flex flex-wrap items-center gap-2 px-3.5 pb-3"
          onSubmit={(e) => { e.preventDefault(); if (key.trim()) void save(); }}
        >
          <Input
            type="password"
            autoComplete="off"
            autoFocus
            aria-label={`${label} API key`}
            placeholder={`${label} API key`}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            disabled={!!busy}
            className="min-w-0 flex-1 basis-[220px] md:h-9"
          />
          <button type="submit" disabled={!!busy || !key.trim()} className={primaryButtonClass}>
            {busy === 'save' ? 'Saving…' : 'Save key'}
          </button>
          <button type="button" onClick={() => { setAdding(false); setKey(''); }} disabled={!!busy} className={quietButtonClass}>Cancel</button>
          {KEY_URLS[row.id] && (
            <a href={KEY_URLS[row.id]} target="_blank" rel="noreferrer" className="w-full text-[12px] text-accent-ink hover:underline">
              Get a {label} key
            </a>
          )}
        </form>
      )}

      {result && (
        <p role={result.ok ? 'status' : 'alert'} className={`px-3.5 pb-3 text-[12px] ${result.ok ? 'text-ok' : 'text-danger-ink'}`}>
          {result.message}
        </p>
      )}
    </div>
  );
}

/** Settings › Models and keys: the engine's key for each model provider. */
export function ProviderKeys() {
  const [list, setList] = useState<ProviderKeyList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProviderKeys().then(setList).catch((e) => setError(errorMessage(e, 'Could not load provider keys.')));
  }, []);

  if (error) return <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>;
  if (!list) return <p className="text-[13px] text-fg-dim">Loading…</p>;

  const replace = (next: ProviderKey) =>
    setList((cur) => cur && { ...cur, providers: cur.providers.map((p) => (p.id === next.id ? next : p)) });

  return (
    <>
      <Group>
        {list.providers.map((row) => (
          <ProviderRow key={row.id} row={row} canEdit={list.can_edit} onChange={replace} />
        ))}
      </Group>
      <p className="mt-3 max-w-[70ch] text-[12px] leading-relaxed text-fg-dim">
        {list.can_edit ? 'Saved keys are encrypted on this engine and used by every agent. ' : 'Only the admin can change these keys. '}
        Keys from the engine's environment or <span className="font-mono">.env</span> take priority and can't be changed here.
        A custom base URL only gets a stored key if it's the provider's own address or listed in{' '}
        <span className="font-mono">COGNITRIX_TRUSTED_BASE_URLS</span>.
      </p>
    </>
  );
}
