import { useEffect, useMemo, useRef, useState } from 'react';
import { useTheme } from '@/context/ThemeContext';
import { errorMessage } from '@/lib/api';
import {
  ARTWORK_IDS, ARTWORK_NAMES, ArtworkError, DEFAULT_STRENGTH, artworkColors, artworkImage, artworkSvg,
  cleanArtworkSvg, type Artwork,
} from '@/lib/theme/artwork';
import { artworkFor, themeFor } from '@/lib/theme/store';
import { themeTokens } from '@/lib/theme/themes';
import { cn } from '@/lib/utils';
import { buttonClass, primaryButtonClass } from './ui';
import { tr } from '@/lib/i18n';

function PasteDialog({ onDone, onClose }: { onDone: (art: Artwork) => void; onClose: () => void }) {
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const use = () => {
    try {
      onDone({ svg: cleanArtworkSvg(text), name: name.trim() || 'Your drawing' });
    } catch (e) {
      setError(e instanceof ArtworkError ? e.message : tr('ui.settings.artworkPicker.thisIsNotAnSvg'));
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={tr('ui.settings.artworkPicker.pasteADrawing')}
        className="flex w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[520px] md:rounded-2xl">
        <header className="border-b border-line px-4 py-3.5">
          <div className="text-[15px] font-semibold">{tr('ui.settings.artworkPicker.pasteADrawing')}</div>
          <p className="mt-0.5 text-[12.5px] text-fg-dim">
            {tr('ui.settings.artworkPicker.svgWithShapesOnlyWrite')}<code className="font-mono text-[11.5px]">accent</code>, <code className="font-mono text-[11.5px]">text</code>, <code className="font-mono text-[11.5px]">warn</code>, <code className="font-mono text-[11.5px]">surface</code>{tr('ui.settings.artworkPicker.soItFollowsTheTheme')}
          </p>
        </header>
        <div className="flex flex-col gap-2 px-4 py-3">
          <input aria-label={tr('ui.settings.artworkPicker.name')} value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('ui.settings.artworkPicker.nameSuchAsLighthouse')} maxLength={48}
            className="h-9 rounded-lg border border-line bg-panel-2 px-2.5 text-[13px] outline-none focus:border-accent" />
          <textarea aria-label={tr('ui.settings.artworkPicker.svg')} value={text} onChange={(e) => { setText(e.target.value); setError(''); }} rows={8} spellCheck={false}
            placeholder={tr('ui.settings.artworkPicker.svgViewbox00400')}
            className="w-full resize-y rounded-lg border border-line bg-panel-2 p-2.5 font-mono text-[11.5px] outline-none focus:border-accent" />
          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button type="button" className={buttonClass} onClick={onClose}>{tr('ui.settings.artworkPicker.cancel')}</button>
          <button type="button" className={primaryButtonClass} disabled={!text.trim()} onClick={use}>{tr('ui.settings.artworkPicker.useDrawing')}</button>
        </footer>
      </div>
    </div>
  );
}

/** Settings > Appearance > Artwork: the drawing for the theme on screen now. */
export function ArtworkPicker() {
  const { theme: appearance, selection, setArtwork, deleteDrawing } = useTheme();
  const shown = themeFor(selection, appearance);
  const current = artworkFor(selection, shown);
  const override = selection.artwork[shown.id];
  const tokens = useMemo(() => themeTokens(shown, appearance), [shown, appearance]);
  const colors = useMemo(() => artworkColors(tokens, tokens.bg), [tokens]);
  const [pasting, setPasting] = useState(false);
  const [error, setError] = useState('');
  const [strength, setStrength] = useState(current?.strength ?? DEFAULT_STRENGTH);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => setStrength(current?.strength ?? DEFAULT_STRENGTH), [current?.strength, shown.id]);

  const run = async (art: Artwork | null) => {
    setError('');
    try { await setArtwork(shown.id, art); } catch (e) { setError(errorMessage(e, tr('ui.settings.artworkPicker.couldNotChangeTheArtwork'))); }
  };
  const choose = (art: Artwork) => void run({ ...art, ...(current?.strength !== undefined ? { strength: current.strength } : {}) });
  const onStrength = (value: number) => {
    setStrength(value);
    clearTimeout(timer.current);
    if (!current) return;
    timer.current = setTimeout(() => void run({ ...current, strength: value }), 350);
  };

  // Drawings still used by some theme (chosen, or a theme's own) can't be removed from the library.
  const inUse = new Set<string>();
  for (const art of Object.values(selection.artwork)) if (art.svg) inUse.add(art.svg);
  for (const t of selection.themes) if (t.artwork?.svg) inUse.add(t.artwork.svg);

  const options: { key: string; label: string; art: Artwork; drawing?: string }[] = [
    { key: 'none', label: tr('ui.settings.artworkPicker.none'), art: { id: 'none' } },
    ...ARTWORK_IDS.map((id) => ({ key: id, label: ARTWORK_NAMES[id], art: { id } })),
  ];
  // The drawing in use first (if the library doesn't have it yet), then every saved drawing.
  if (current?.svg && !selection.drawings.some((d) => d.svg === current.svg)) {
    options.push({ key: 'own', label: current.name || 'Your drawing', art: current });
  }
  for (const d of selection.drawings) {
    options.push({ key: `drawing:${d.key}`, label: d.name, art: { svg: d.svg, name: d.name }, drawing: d.key });
  }
  const selectedKey = !current ? 'none'
    : current.svg ? (options.find((o) => o.art.svg === current.svg)?.key ?? 'own')
      : current.id;

  const forget = async (key: string) => {
    setError('');
    try { await deleteDrawing(key); } catch (e) { setError(errorMessage(e, tr('ui.settings.artworkPicker.couldNotRemoveThisDrawing'))); }
  };

  return (
    <>
      <div className="rounded-[12px] border border-line bg-panel">
        <div className="p-3.5">
          <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-[13.5px] font-medium">{tr('ui.settings.artworkPicker.artworkForName', { name: shown.name })}</span>
            {override && (
              <button type="button" className="text-[12px] text-accent-ink hover:underline" onClick={() => void run(null)}>
                {tr('ui.settings.artworkPicker.useTheThemeSOwn')}
              </button>
            )}
          </div>
          <p className="mb-2.5 text-[12px] text-fg-dim">{tr('ui.settings.artworkPicker.shownAtTheTopOf')}</p>
          <div role="radiogroup" aria-label={tr('ui.settings.artworkPicker.artworkForName', { name: shown.name })} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {options.map((o) => {
              const svg = artworkSvg(o.art);
              const removable = !!o.drawing && selectedKey !== o.key && !inUse.has(o.art.svg ?? '');
              return (
                <div key={o.key} className="group relative">
                <button type="button" role="radio" aria-checked={selectedKey === o.key} onClick={() => choose(o.art)}
                  className={cn('w-full rounded-[10px] border p-1.5 text-left transition-colors',
                    selectedKey === o.key ? 'border-accent ring-1 ring-accent' : 'border-line hover:border-fg-dim')}>
                  <span className="relative grid aspect-[5/2] place-items-center overflow-hidden rounded-md border border-line" style={{ background: tokens.bg }}>
                    {svg ? <img src={artworkImage(svg, colors)} alt="" className="h-full w-full object-cover" />
                      : <span className="text-[11.5px]" style={{ color: tokens['fg-dim'] }}>{tr('ui.settings.artworkPicker.noArtwork')}</span>}
                  </span>
                  <span className={cn('mt-1 block truncate text-[12px]', removable && 'pr-5')} title={o.label}>{o.label}</span>
                </button>
                {removable && (
                  <button type="button" aria-label={tr('ui.settings.artworkPicker.removeLabelFromSavedDrawings', { label: o.label })} title={tr('ui.settings.artworkPicker.removeFromSavedDrawings')}
                    onClick={() => void forget(o.drawing!)}
                    className="absolute bottom-1.5 right-1.5 grid h-5 w-5 place-items-center rounded text-[13px] leading-none text-fg-dim hover:bg-panel-2 hover:text-danger-ink">
                    ×
                  </button>
                )}
                </div>
              );
            })}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-line px-3.5 py-3">
          <label htmlFor="artwork-strength" className="text-[12.5px] text-fg-dim">{tr('ui.settings.artworkPicker.strength')}</label>
          <input id="artwork-strength" type="range" min={20} max={100} step={5} value={strength} disabled={!current}
            onChange={(e) => onStrength(Number(e.target.value))} className="min-w-[140px] flex-1 accent-accent-ink" />
          <button type="button" className={buttonClass} onClick={() => setPasting(true)}>{tr('ui.settings.artworkPicker.pasteSvg')}</button>
        </div>
        <p className="border-t border-line px-3.5 py-2.5 text-[12px] text-fg-dim">
          {tr('ui.settings.artworkPicker.orAskAnyAgentFor')}
        </p>
      </div>
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
      {pasting && <PasteDialog onClose={() => setPasting(false)} onDone={(art) => { setPasting(false); choose(art); }} />}
    </>
  );
}
