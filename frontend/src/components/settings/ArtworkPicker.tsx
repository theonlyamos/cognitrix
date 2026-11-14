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

function PasteDialog({ onDone, onClose }: { onDone: (art: Artwork) => void; onClose: () => void }) {
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const use = () => {
    try {
      onDone({ svg: cleanArtworkSvg(text), name: name.trim() || 'Your drawing' });
    } catch (e) {
      setError(e instanceof ArtworkError ? e.message : 'This is not an SVG drawing.');
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 md:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label="Paste a drawing"
        className="flex w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[520px] md:rounded-2xl">
        <header className="border-b border-line px-4 py-3.5">
          <div className="text-[15px] font-semibold">Paste a drawing</div>
          <p className="mt-0.5 text-[12.5px] text-fg-dim">
            SVG with shapes only. Write colours as theme words (<code className="font-mono text-[11.5px]">accent</code>, <code className="font-mono text-[11.5px]">text</code>, <code className="font-mono text-[11.5px]">warn</code>, <code className="font-mono text-[11.5px]">surface</code>…) so it follows the theme. Wide drawings (about 400 × 160) fit best.
          </p>
        </header>
        <div className="flex flex-col gap-2 px-4 py-3">
          <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, such as Lighthouse" maxLength={48}
            className="h-9 rounded-lg border border-line bg-panel-2 px-2.5 text-[13px] outline-none focus:border-accent" />
          <textarea aria-label="SVG" value={text} onChange={(e) => { setText(e.target.value); setError(''); }} rows={8} spellCheck={false}
            placeholder={'<svg viewBox="0 0 400 160">\n  <circle cx="320" cy="50" r="24" fill="accent"/>\n</svg>'}
            className="w-full resize-y rounded-lg border border-line bg-panel-2 p-2.5 font-mono text-[11.5px] outline-none focus:border-accent" />
          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        </div>
        <footer className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button type="button" className={buttonClass} onClick={onClose}>Cancel</button>
          <button type="button" className={primaryButtonClass} disabled={!text.trim()} onClick={use}>Use drawing</button>
        </footer>
      </div>
    </div>
  );
}

/** Settings > Appearance > Artwork: the drawing for the theme on screen now. */
export function ArtworkPicker() {
  const { theme: appearance, selection, setArtwork } = useTheme();
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
    try { await setArtwork(shown.id, art); } catch (e) { setError(errorMessage(e, 'Could not change the artwork.')); }
  };
  const choose = (art: Artwork) => void run({ ...art, ...(current?.strength !== undefined ? { strength: current.strength } : {}) });
  const onStrength = (value: number) => {
    setStrength(value);
    clearTimeout(timer.current);
    if (!current) return;
    timer.current = setTimeout(() => void run({ ...current, strength: value }), 350);
  };

  const options: { key: string; label: string; art: Artwork }[] = [
    { key: 'none', label: 'None', art: { id: 'none' } },
    ...ARTWORK_IDS.map((id) => ({ key: id, label: ARTWORK_NAMES[id], art: { id } })),
  ];
  if (current?.svg) options.push({ key: 'own', label: current.name || 'Your drawing', art: current });
  const selectedKey = current ? (current.svg ? 'own' : current.id) : 'none';

  return (
    <>
      <div className="rounded-[12px] border border-line bg-panel">
        <div className="p-3.5">
          <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-[13.5px] font-medium">Artwork for {shown.name}</span>
            {override && (
              <button type="button" className="text-[12px] text-accent-ink hover:underline" onClick={() => void run(null)}>
                Use the theme’s own
              </button>
            )}
          </div>
          <p className="mb-2.5 text-[12px] text-fg-dim">Shown at the top of the sidebar and above new chats. It is drawn in this theme’s colours.</p>
          <div role="radiogroup" aria-label={`Artwork for ${shown.name}`} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {options.map((o) => {
              const svg = artworkSvg(o.art);
              return (
                <button key={o.key} type="button" role="radio" aria-checked={selectedKey === o.key} onClick={() => choose(o.art)}
                  className={cn('rounded-[10px] border p-1.5 text-left transition-colors',
                    selectedKey === o.key ? 'border-accent ring-1 ring-accent' : 'border-line hover:border-fg-dim')}>
                  <span className="relative grid aspect-[5/2] place-items-center overflow-hidden rounded-md border border-line" style={{ background: tokens.bg }}>
                    {svg ? <img src={artworkImage(svg, colors)} alt="" className="h-full w-full object-cover" />
                      : <span className="text-[11.5px]" style={{ color: tokens['fg-dim'] }}>No artwork</span>}
                  </span>
                  <span className="mt-1 block truncate text-[12px]" title={o.label}>{o.label}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-line px-3.5 py-3">
          <label htmlFor="artwork-strength" className="text-[12.5px] text-fg-dim">Strength</label>
          <input id="artwork-strength" type="range" min={20} max={100} step={5} value={strength} disabled={!current}
            onChange={(e) => onStrength(Number(e.target.value))} className="min-w-[140px] flex-1 accent-accent-ink" />
          <button type="button" className={buttonClass} onClick={() => setPasting(true)}>Paste SVG</button>
        </div>
        <p className="border-t border-line px-3.5 py-2.5 text-[12px] text-fg-dim">
          Or ask any agent, for example “draw me a lighthouse for my theme”.
        </p>
      </div>
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
      {pasting && <PasteDialog onClose={() => setPasting(false)} onDone={(art) => { setPasting(false); choose(art); }} />}
    </>
  );
}
