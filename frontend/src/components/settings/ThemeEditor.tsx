import { useEffect, useMemo, useRef, useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { errorMessage } from '@/lib/api';
import { isHex, normalizeHex } from '@/lib/theme/color';
import {
  PRESETS, TOKENS, TOKEN_LABELS, contrastWarnings, deriveTokens, isTokenColor,
  type Appearance, type ThemeFile, type ThemeVariant, type TokenName,
} from '@/lib/theme/themes';
import { cn } from '@/lib/utils';
import { buttonClass, primaryButtonClass } from './ui';

const BASE_FIELDS: { key: 'background' | 'text' | 'accent'; label: string }[] = [
  { key: 'background', label: 'Background' },
  { key: 'text', label: 'Text' },
  { key: 'accent', label: 'Accent' },
];

/** A colour swatch (opens the system picker) beside a hex box. */
function ColorField({ label, value, placeholder, onChange, allowRgba }: {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
  allowRgba?: boolean;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const shown = value || placeholder || '';
  const swatch = normalizeHex(shown) ?? (isHex(shown.slice(0, 7)) ? shown.slice(0, 7) : '#000000');
  const commit = (raw: string) => {
    const hex = normalizeHex(raw);
    if (hex) onChange(hex);
    else if (allowRgba && isTokenColor(raw)) onChange(raw.trim());
    else if (!raw.trim()) onChange('');
    else setText(value);
  };
  return (
    <label className="flex items-center gap-2.5 py-1.5">
      <span className="min-w-0 flex-1 text-[12.5px]">{label}</span>
      <span className="relative h-7 w-7 flex-none overflow-hidden rounded-md border border-line" style={{ background: shown || 'transparent' }}>
        <input
          type="color"
          aria-label={`${label} colour`}
          value={swatch}
          onChange={(e) => onChange(e.target.value)}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
      </span>
      <input
        type="text"
        aria-label={`${label} value`}
        value={text}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') commit((e.target as HTMLInputElement).value); }}
        className="h-8 w-[118px] flex-none rounded-md border border-line bg-panel-2 px-2 font-mono text-[11.5px] outline-none focus:border-accent"
      />
    </label>
  );
}

/** A variant to start from when a theme gains an appearance it didn't have. */
function starterVariant(appearance: Appearance): ThemeVariant {
  const signal = PRESETS[0][appearance]!;
  return { background: signal.background, text: signal.text, accent: signal.accent };
}

/**
 * The theme editor: a panel beside the app, which shows the theme live while
 * you edit. Save keeps it (on the account when signed in) and uses it.
 */
export function ThemeEditor({ initial, isNew, onClose }: { initial: ThemeFile; isNew: boolean; onClose: () => void }) {
  const { setPreview, saveTheme, pickTheme, theme: shownAppearance } = useTheme();
  const [draft, setDraft] = useState<ThemeFile>(initial);
  const [tab, setTab] = useState<Appearance>(initial[shownAppearance] ? shownAppearance : initial.light ? 'light' : 'dark');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const panel = useRef<HTMLDivElement>(null);

  const variant = draft[tab];
  const derived = useMemo(() => (variant ? deriveTokens(variant, tab, draft.contrast ?? 50) : null), [variant, tab, draft.contrast]);
  const warnings = derived ? contrastWarnings(derived) : [];

  // Live preview of the whole app; put things back when the editor closes.
  useEffect(() => {
    if (draft[tab]) setPreview({ theme: draft, appearance: tab });
    else setPreview(null);
  }, [draft, tab, setPreview]);
  useEffect(() => () => setPreview(null), [setPreview]);

  useEffect(() => {
    panel.current?.querySelector<HTMLElement>('input, button')?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const setVariant = (next: ThemeVariant | undefined) => setDraft((d) => {
    const copy = { ...d };
    if (next) copy[tab] = next; else delete copy[tab];
    return copy;
  });
  const setBase = (key: 'background' | 'text' | 'accent', value: string) => {
    if (!variant || !value) return;
    setVariant({ ...variant, [key]: value });
  };
  const setToken = (name: TokenName, value: string) => {
    if (!variant) return;
    const colors = { ...(variant.colors ?? {}) };
    if (value) colors[name] = value; else delete colors[name];
    setVariant({ ...variant, colors: Object.keys(colors).length ? colors : undefined });
  };
  const setFont = (key: 'ui' | 'code', value: string) => setDraft((d) => {
    const fonts = { ...(d.fonts ?? {}), [key]: value.trim() || null };
    const copy: ThemeFile = { ...d, fonts };
    if (!fonts.ui && !fonts.code) delete copy.fonts;
    return copy;
  });

  const otherHas = !!draft[tab === 'light' ? 'dark' : 'light'];

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const file: ThemeFile = { ...draft, name: draft.name.trim() || 'My theme' };
      if (file.light?.colors && !Object.keys(file.light.colors).length) delete file.light.colors;
      if (file.dark?.colors && !Object.keys(file.dark.colors).length) delete file.dark.colors;
      await saveTheme(file);
      if (file.light) await pickTheme('light', file.id);
      if (file.dark) await pickTheme('dark', file.id);
      onClose();
    } catch (e) {
      setError(errorMessage(e, 'Could not save this theme.'));
      setBusy(false);
    }
  };

  return (
    <div
      ref={panel}
      role="dialog"
      aria-modal="false"
      aria-label={isNew ? 'Create theme' : `Edit ${initial.name}`}
      className="fixed inset-x-0 bottom-0 z-50 flex max-h-[78dvh] flex-col rounded-t-2xl border border-line bg-panel shadow-panel md:inset-x-auto md:bottom-3 md:right-3 md:top-3 md:max-h-none md:w-[380px] md:rounded-2xl"
    >
      <header className="flex flex-none items-center gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10.5px] tracking-[0.14em] text-fg-dim">{isNew ? 'NEW THEME' : 'EDIT THEME'}</div>
          <input
            aria-label="Theme name"
            value={draft.name}
            maxLength={48}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            className="mt-0.5 w-full bg-transparent text-[15px] font-semibold outline-none"
          />
        </div>
        <button type="button" aria-label="Close without saving" onClick={onClose}
          className="grid h-9 w-9 place-items-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg">
          <X size={16} />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div role="tablist" aria-label="Appearance" className="mb-3 grid grid-cols-2 gap-0.5 rounded-lg border border-line p-0.5">
          {(['light', 'dark'] as const).map((m) => (
            <button key={m} type="button" role="tab" aria-selected={tab === m} onClick={() => setTab(m)}
              className={cn('h-8 rounded-md text-[12.5px]', tab === m ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:text-fg')}>
              {m === 'light' ? 'Light' : 'Dark'}{draft[m] ? '' : ' (off)'}
            </button>
          ))}
        </div>

        {!variant ? (
          <div className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-[12.5px] text-fg-dim">
            <p>This theme has no {tab} version, so {tab} mode keeps using another theme.</p>
            <button type="button" className={cn(buttonClass, 'mt-2')} onClick={() => setVariant(starterVariant(tab))}>
              Add a {tab} version
            </button>
          </div>
        ) : (
          <>
            {BASE_FIELDS.map((f) => (
              <ColorField key={f.key} label={f.label} value={variant[f.key]} onChange={(v) => setBase(f.key, v)} />
            ))}
            <label className="block py-2">
              <span className="flex items-center justify-between text-[12.5px]">
                <span>Contrast</span>
                <span className="font-mono text-[11px] text-fg-dim">{draft.contrast ?? 50}</span>
              </span>
              <input type="range" min={0} max={100} step={5} value={draft.contrast ?? 50} aria-label="Contrast"
                onChange={(e) => setDraft((d) => ({ ...d, contrast: Number(e.target.value) }))}
                className="mt-1 w-full accent-accent-ink" />
              <span className="block text-[11.5px] text-fg-dim">How strongly borders and secondary text stand out.</span>
            </label>

            {warnings.length > 0 && (
              <ul role="status" className="my-2 space-y-1 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[12px] text-warn-ink">
                {warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}

            <details className="group mt-2 rounded-lg border border-line">
              <summary className="cursor-pointer select-none px-3 py-2 text-[12.5px] font-medium">
                Advanced colours
                <span className="ml-1 font-normal text-fg-dim">· {Object.keys(variant.colors ?? {}).length} set by hand</span>
              </summary>
              <div className="border-t border-line px-3 py-1">
                <p className="py-1.5 text-[11.5px] leading-snug text-fg-dim">Empty boxes are worked out from the three colours above. Set one to override it.</p>
                {TOKENS.filter((n) => n !== 'bg' && n !== 'fg' && n !== 'accent').map((name) => (
                  <div key={name} className="flex items-center">
                    <div className="min-w-0 flex-1">
                      <ColorField
                        label={TOKEN_LABELS[name]}
                        value={variant.colors?.[name] ?? ''}
                        placeholder={derived?.[name]}
                        allowRgba={name === 'line'}
                        onChange={(v) => setToken(name, v)}
                      />
                    </div>
                    <button type="button" aria-label={`Reset ${TOKEN_LABELS[name]}`} disabled={!variant.colors?.[name]}
                      onClick={() => setToken(name, '')}
                      className="ml-1 grid h-7 w-7 flex-none place-items-center rounded text-fg-dim hover:text-fg disabled:opacity-25">
                      <RotateCcw size={13} />
                    </button>
                  </div>
                ))}
              </div>
            </details>

            {otherHas && (
              <button type="button" className="mt-2 text-[12px] text-danger-ink hover:underline" onClick={() => setVariant(undefined)}>
                Remove the {tab} version
              </button>
            )}
          </>
        )}

        <div className="mt-4 font-mono text-[10.5px] tracking-[0.14em] text-fg-dim">FONTS</div>
        <p className="mt-1 text-[11.5px] text-fg-dim">The name of a font installed on your computer. Leave empty for the default.</p>
        {(['ui', 'code'] as const).map((key) => (
          <label key={key} className="flex items-center gap-2.5 py-1.5">
            <span className="min-w-0 flex-1 text-[12.5px]">{key === 'ui' ? 'Interface font' : 'Code font'}</span>
            <input
              value={draft.fonts?.[key] ?? ''}
              placeholder={key === 'ui' ? 'Space Grotesk' : 'JetBrains Mono'}
              maxLength={64}
              onChange={(e) => setFont(key, e.target.value)}
              className="h-8 w-[160px] flex-none rounded-md border border-line bg-panel-2 px-2 text-[12px] outline-none focus:border-accent"
            />
          </label>
        ))}
      </div>

      {error && <p role="alert" className="px-4 pb-2 text-[12.5px] text-danger-ink">{error}</p>}
      <footer className="flex flex-none items-center justify-end gap-2 border-t border-line px-4 py-3">
        <button type="button" className={buttonClass} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButtonClass} disabled={busy || !draft.name.trim()} onClick={() => void save()}>
          {busy ? 'Saving…' : 'Save and use'}
        </button>
      </footer>
    </div>
  );
}
