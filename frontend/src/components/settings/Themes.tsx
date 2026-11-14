import { useMemo, useRef, useState } from 'react';
import { Plus, Upload } from 'lucide-react';
import { ChatRowMenu, type ChatRowAction } from '@/components/ChatRowMenu';
import { useTheme } from '@/context/ThemeContext';
import { errorMessage } from '@/lib/api';
import { useT } from '@/lib/i18n';
import {
  PRESETS, ThemeFormatError, deriveTokens, duplicateTheme, exportTheme, importTheme, isPreset, uniqueId,
  type Appearance, type ThemeFile,
} from '@/lib/theme/themes';
import { cn } from '@/lib/utils';
import { ThemeEditor } from './ThemeEditor';
import { buttonClass, primaryButtonClass } from './ui';

/** A small picture of the app in a theme: sidebar, accent bar, two text lines. */
export function ThemeSwatch({ theme, appearance, className }: { theme: ThemeFile; appearance: Appearance; className?: string }) {
  const variant = theme[appearance] ?? theme.light ?? theme.dark!;
  const t = deriveTokens(variant, theme[appearance] ? appearance : theme.light ? 'light' : 'dark', theme.contrast ?? 50);
  return (
    <span className={cn('grid h-full w-full grid-cols-[28%_1fr]', className)} style={{ background: t.bg }}>
      <span style={{ background: t['panel-2'] }} />
      <span className="flex flex-col gap-1.5 p-2">
        <span className="h-1.5 w-1/2 rounded-full" style={{ background: t.accent }} />
        <span className="h-1 w-3/4 rounded-full" style={{ background: t.fg, opacity: 0.55 }} />
        <span className="h-1 w-2/3 rounded-full" style={{ background: t['fg-dim'], opacity: 0.7 }} />
      </span>
    </span>
  );
}

function download(theme: ThemeFile) {
  const blob = new Blob([exportTheme(theme)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${theme.id}.cognitrix-theme.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Paste or pick a file: a Cognitrix theme, a Codex theme string or a VS Code colour theme. */
function ImportDialog({ onImported, onClose }: { onImported: (theme: ThemeFile) => void; onClose: () => void }) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const fileName = useRef('Imported theme');

  const tryImport = (value: string) => {
    try {
      onImported(importTheme(value, fileName.current));
    } catch (e) {
      setError(e instanceof ThemeFormatError ? e.message : 'This is not a theme.');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label="Import a theme"
        className="flex w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[520px] md:rounded-2xl">
        <header className="border-b border-line px-4 py-3.5">
          <div className="text-[15px] font-semibold">Import a theme</div>
          <p className="mt-0.5 text-[12.5px] text-fg-dim">
            Paste a Cognitrix theme, a Codex app theme (<code className="font-mono text-[11.5px]">codex-theme-v1:…</code>) or a VS Code colour theme, or choose a file.
          </p>
        </header>
        <div className="px-4 py-3">
          <textarea
            aria-label="Theme to import"
            value={text}
            onChange={(e) => { setText(e.target.value); setError(''); }}
            rows={8}
            spellCheck={false}
            placeholder='{ "name": "Nightfall", "dark": { "background": "#1a1b26", "text": "#c0caf5", "accent": "#7aa2f7" } }'
            className="w-full resize-y rounded-lg border border-line bg-panel-2 p-2.5 font-mono text-[11.5px] outline-none focus:border-accent"
          />
          {error && <p role="alert" className="mt-1.5 text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex items-center gap-2 border-t border-line px-4 py-3">
          <label className={cn(buttonClass, 'cursor-pointer')}>
            Choose file
            <input type="file" accept=".json,.jsonc,.txt,application/json,text/plain" className="sr-only"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                e.target.value = '';
                if (!file) return;
                if (file.size > 512 * 1024) { setError('That file is too large to be a theme.'); return; }
                fileName.current = file.name.replace(/\.(cognitrix-theme\.)?(json|jsonc|txt)$/i, '').replace(/[-_]+/g, ' ') || 'Imported theme';
                const value = await file.text();
                setText(value);
                tryImport(value);
              }} />
          </label>
          <span className="flex-1" />
          <button type="button" className={buttonClass} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryButtonClass} disabled={!text.trim()} onClick={() => tryImport(text)}>Import</button>
        </footer>
      </div>
    </div>
  );
}

function ThemeTile({ theme, appearance, selected, onPick, actions, moreLabel }: {
  theme: ThemeFile;
  appearance: Appearance;
  selected: boolean;
  onPick: () => void;
  actions: ChatRowAction[];
  moreLabel: string;
}) {
  return (
    <div className={cn('group relative rounded-[10px] border p-1.5 transition-colors',
      selected ? 'border-accent ring-1 ring-accent' : 'border-line hover:border-fg-dim')}>
      <button type="button" role="radio" aria-checked={selected} onClick={onPick} className="block w-full text-left">
        <span className="relative block h-12 overflow-hidden rounded-md border border-line">
          <ThemeSwatch theme={theme} appearance={appearance} />
        </span>
        <span className="mt-1 block truncate pr-7 text-[12px]" title={theme.name}>{theme.name}</span>
      </button>
      <div className="absolute bottom-0.5 right-0 opacity-100 md:opacity-0 md:focus-within:opacity-100 md:group-hover:opacity-100 [&>div]:mr-0.5 [&_button[aria-haspopup]]:h-7 [&_button[aria-haspopup]]:w-7 [&_button[aria-haspopup]]:border-0">
        <ChatRowMenu label={moreLabel} actions={actions} />
      </div>
    </div>
  );
}

/** Settings > Appearance: pick a theme for light and for dark, create, import, export. */
export function ThemeLibrary() {
  const { selection, themes, pickTheme, deleteTheme, synced } = useTheme();
  const t = useT();
  const [editing, setEditing] = useState<{ theme: ThemeFile; isNew: boolean } | null>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const takenIds = useMemo(() => themes.map((x) => x.id), [themes]);

  const run = async (work: () => Promise<void>) => {
    setError('');
    try { await work(); } catch (e) { setError(errorMessage(e, 'Could not change the theme.')); }
  };

  const createNew = () => {
    const base = duplicateTheme(PRESETS[0], takenIds, 'My theme');
    // Start from Signal's three colours but let everything else be derived.
    for (const m of ['light', 'dark'] as const) {
      const v = base[m]!;
      base[m] = { background: v.background, text: v.text, accent: v.accent };
    }
    setEditing({ theme: base, isNew: true });
  };

  const actionsFor = (theme: ThemeFile): ChatRowAction[] => {
    const own = !isPreset(theme.id);
    const list: ChatRowAction[] = [];
    if (own) list.push({ label: t('appearance.themes.edit'), onSelect: () => setEditing({ theme, isNew: false }) });
    list.push({ label: t('appearance.themes.duplicate'), onSelect: () => setEditing({ theme: duplicateTheme(theme, takenIds), isNew: true }) });
    list.push({
      label: t('appearance.themes.copy'),
      onSelect: () => void navigator.clipboard?.writeText(exportTheme(theme)).then(
        () => setNotice(`Copied “${theme.name}”. Paste it into Import on another account.`),
        () => setError('Could not copy. Use Download instead.'),
      ),
    });
    list.push({ label: t('appearance.themes.download'), onSelect: () => download(theme) });
    if (own) list.push({ label: t('appearance.themes.delete'), danger: true, onSelect: () => void run(() => deleteTheme(theme.id)) });
    return list;
  };

  const row = (appearance: Appearance) => {
    const choices = themes.filter((x) => x[appearance]);
    const label = t(appearance === 'light' ? 'appearance.lightTheme' : 'appearance.darkTheme');
    return (
      <div className="p-3.5">
        <div className="mb-2.5 text-[13.5px] font-medium">{label}</div>
        <div role="radiogroup" aria-label={label} className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
          {choices.map((theme) => (
            <ThemeTile
              key={theme.id}
              theme={theme}
              appearance={appearance}
              selected={selection[appearance] === theme.id || (!choices.some((c) => c.id === selection[appearance]) && theme.id === 'signal')}
              onPick={() => void run(() => pickTheme(appearance, theme.id))}
              actions={actionsFor(theme)}
              moreLabel={t('appearance.themes.more', { name: theme.name })}
            />
          ))}
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="divide-y divide-line rounded-[12px] border border-line bg-panel">
        {row('light')}
        {row('dark')}
        <div className="flex flex-wrap items-center gap-2 px-3.5 py-3">
          <button type="button" className={buttonClass} onClick={createNew}><Plus size={14} className="mr-1.5" />{t('appearance.themes.create')}</button>
          <button type="button" className={buttonClass} onClick={() => setImporting(true)}><Upload size={14} className="mr-1.5" />{t('appearance.themes.import')}</button>
          <span className="min-w-[200px] flex-1 text-[12px] leading-snug text-fg-dim">
            {t(synced ? 'appearance.themes.synced' : 'appearance.themes.local')}
          </span>
        </div>
      </div>
      {(notice || error) && (
        <p role={error ? 'alert' : 'status'} className={cn('mt-2 text-[12.5px]', error ? 'text-danger-ink' : 'text-fg-dim')}>{error || notice}</p>
      )}
      {importing && (
        <ImportDialog
          onClose={() => setImporting(false)}
          onImported={(theme) => {
            setImporting(false);
            setEditing({ theme: { ...theme, id: uniqueId(theme.id, takenIds) }, isNew: true });
          }}
        />
      )}
      {editing && <ThemeEditor key={editing.theme.id} initial={editing.theme} isNew={editing.isNew} onClose={() => setEditing(null)} />}
    </>
  );
}
