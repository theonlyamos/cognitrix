import { useEffect, useId, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { MODELS } from '@/lib/providers';
import { cn } from '@/lib/utils';

const CUSTOM = '__custom__';

interface LiveModels {
  models: string[];
  live: boolean;
  reason?: string;
}

// One request per provider per page load; the server caches for 10 minutes.
const liveCache = new Map<string, Promise<LiveModels>>();

export function fetchProviderModels(provider: string): Promise<LiveModels> {
  if (!provider) return Promise.resolve({ models: [], live: false });
  let pending = liveCache.get(provider);
  if (!pending) {
    pending = api
      .get<LiveModels>(`/providers/${encodeURIComponent(provider)}/models`)
      .then((r) => ({ models: Array.isArray(r.data?.models) ? r.data.models : [], live: Boolean(r.data?.live), reason: r.data?.reason }))
      .catch(() => ({ models: [], live: false }));
    liveCache.set(provider, pending);
  }
  return pending;
}

/** Test hook: forget fetched lists. */
export function clearModelCache() {
  liveCache.clear();
}

interface Props {
  provider: string;
  value: string;
  onChange: (model: string) => void;
  id?: string;
  /** Accessible name when there's no visible <label htmlFor>. */
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  /** Extra choices shown first, e.g. [{ value: '', label: 'App setting' }]. */
  leading?: { value: string; label: string }[];
}

/**
 * A model choice: the provider's recent models, then whatever the provider
 * lists for the engine's key, then "Custom…" for any other model name.
 */
export default function ModelPicker({ provider, value, onChange, id, ariaLabel, disabled, className, leading = [] }: Props) {
  const autoId = useId();
  const selectId = id || `model-${autoId}`;
  const [live, setLive] = useState<string[]>([]);
  const [customOpen, setCustomOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    setLive([]);
    fetchProviderModels(provider).then((r) => { if (alive) setLive(r.models); });
    return () => { alive = false; };
  }, [provider]);

  const recent = MODELS[provider] || [];
  const more = useMemo(() => live.filter((m) => !recent.includes(m)), [live, recent]);
  const known = leading.some((o) => o.value === value) || recent.includes(value) || more.includes(value);
  const custom = customOpen || (!known && value !== '');

  const pick = (next: string) => {
    if (next === CUSTOM) {
      setCustomOpen(true);
      if (known) onChange('');
      return;
    }
    setCustomOpen(false);
    onChange(next);
  };

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <select
        id={selectId}
        aria-label={ariaLabel}
        value={custom ? CUSTOM : value}
        disabled={disabled}
        onChange={(e) => pick(e.target.value)}
        className="h-11 w-full rounded border border-line bg-bg px-3 font-mono text-[13px] text-fg transition-colors focus:border-accent disabled:opacity-50 md:h-10"
      >
        {leading.map((o) => <option key={`lead-${o.value}`} value={o.value}>{o.label}</option>)}
        {!known && !custom && value === '' && leading.length === 0 && <option value="">Choose a model</option>}
        {recent.length > 0 && (
          <optgroup label="Recent">
            {recent.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        )}
        {more.length > 0 && (
          <optgroup label="From your account">
            {more.map((m) => <option key={m} value={m}>{m}</option>)}
          </optgroup>
        )}
        <option value={CUSTOM}>Custom…</option>
      </select>
      {custom && (
        <input
          aria-label="Custom model name"
          value={value}
          autoFocus={customOpen}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Type a model name"
          className="h-11 w-full rounded border border-line bg-bg px-3 font-mono text-[13px] text-fg placeholder:text-fg-dim focus:border-accent md:h-10"
        />
      )}
    </div>
  );
}
