import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Reply } from 'lucide-react';
import { cleanQuote, replySourceOf } from '@/lib/quoteReply';
import { tr } from '@/lib/i18n';

interface Spot {
  text: string;
  top: number;
  left: number;
  below: boolean;
}

const BUTTON_GAP = 8;
const EDGE = 12;

/** The picked text when it lies in an agent's reply (marked data-reply-source). */
function readSelection(): { text: string; rect: DOMRect; box: DOMRect } | null {
  const sel = typeof window === 'undefined' ? null : window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  if (!replySourceOf(sel.anchorNode) || !replySourceOf(sel.focusNode)) return null;
  const text = cleanQuote(sel.toString());
  if (!text) return null;
  const range = sel.getRangeAt(0);
  const rects = Array.from(range.getClientRects?.() ?? []).filter((r) => r.width > 0 || r.height > 0);
  const box = range.getBoundingClientRect?.() ?? new DOMRect();
  return { text, rect: rects[0] ?? box, box };
}

/**
 * A small "Reply" button over text picked in an agent's reply. Pressing it
 * hands the text to onReply (which quotes it in the message box) and clears
 * the selection. Works with mouse, keyboard (Shift+arrows) and touch.
 */
export function SelectionReply({ onReply, disabled = false }: { onReply: (text: string) => void; disabled?: boolean }) {
  const [spot, setSpot] = useState<Spot | null>(null);
  const pointerDown = useRef(false);
  const frame = useRef(0);

  const measure = useCallback(() => {
    window.cancelAnimationFrame(frame.current);
    frame.current = window.requestAnimationFrame(() => {
      const picked = disabled || pointerDown.current ? null : readSelection();
      if (!picked) { setSpot(null); return; }
      // rect: the first line picked; box: the whole selection.
      const { rect, box } = picked;
      // On touch screens the phone's own copy menu sits above the selection,
      // so the button goes under it; elsewhere above, unless there's no room.
      const coarse = window.matchMedia?.('(pointer: coarse)').matches ?? false;
      const below = coarse || rect.top < 56;
      const top = below ? Math.max(box.bottom, rect.bottom) + BUTTON_GAP : rect.top - BUTTON_GAP;
      const left = Math.min(Math.max(rect.left + Math.min(rect.width, 240) / 2, EDGE + 40), window.innerWidth - EDGE - 40);
      setSpot({ text: picked.text, top, left, below });
    });
  }, [disabled]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if ((e.target as HTMLElement | null)?.closest?.('[data-selection-reply]')) return;
      pointerDown.current = true;
      setSpot(null);
    };
    const onUp = () => { pointerDown.current = false; measure(); };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSpot(null);
      else measure();
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('pointerup', onUp, true);
    document.addEventListener('pointercancel', onUp, true);
    document.addEventListener('selectionchange', measure);
    document.addEventListener('keyup', onKey);
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    return () => {
      window.cancelAnimationFrame(frame.current);
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('pointerup', onUp, true);
      document.removeEventListener('pointercancel', onUp, true);
      document.removeEventListener('selectionchange', measure);
      document.removeEventListener('keyup', onKey);
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [measure]);

  if (!spot) return null;
  return createPortal(
    <button
      type="button"
      data-selection-reply=""
      // Keep the selection while the button is pressed.
      onPointerDown={(e) => e.preventDefault()}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => {
        onReply(spot.text);
        window.getSelection()?.removeAllRanges();
        setSpot(null);
      }}
      style={{ top: spot.top, left: spot.left }}
      className={`fixed z-[80] flex min-h-11 -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-panel px-3.5 text-[13px] font-medium text-fg shadow-lg transition-colors hover:bg-panel-2 md:min-h-0 md:py-1.5 ${spot.below ? '' : '-translate-y-full'}`}
    >
      <Reply size={14} aria-hidden />
      {tr('ui.selectionReply.reply')}
    </button>,
    document.body,
  );
}

/** Quotes waiting in the message box, each with an ✕ to take it out. */
export function QuoteChips({ quotes, onRemove, variant = 'full' }: { quotes: string[]; onRemove: (index: number) => void; variant?: 'full' | 'chill' }) {
  if (!quotes.length) return null;
  const chill = variant === 'chill';
  return (
    <div className={chill ? 'mb-2 flex flex-col gap-1.5 px-1' : 'flex flex-col gap-1.5 border-b border-line px-3 pb-2 pt-2.5'}>
      {quotes.map((quote, index) => (
        <div
          key={`${index}-${quote.slice(0, 24)}`}
          className={chill
            ? 'chill-glass flex items-start gap-2 rounded-2xl py-1.5 pl-3 pr-1'
            : 'flex items-start gap-2 rounded-md border border-line bg-panel-2 py-1.5 pl-2.5 pr-1'}
        >
          <Reply size={13} aria-hidden className="mt-[3px] flex-none text-fg-dim" />
          <p className="line-clamp-2 min-w-0 flex-1 whitespace-pre-wrap break-words border-l-2 border-accent-ink pl-2 text-[12.5px] leading-snug text-fg-dim" title={quote}>
            {quote}
          </p>
          <button
            type="button"
            onClick={() => onRemove(index)}
            aria-label={tr('ui.selectionReply.removeQuote')}
            title={tr('ui.selectionReply.removeQuote')}
            className="grid h-11 w-11 flex-none place-items-center rounded-full text-fg-dim transition-colors hover:text-danger-ink md:h-6 md:w-6"
          >
            <span aria-hidden>✕</span>
          </button>
        </div>
      ))}
    </div>
  );
}

/** The quotes at the top of a sent message, as shown in the chat. */
export function SentQuotes({ quotes, className = '' }: { quotes: string[]; className?: string }) {
  if (!quotes.length) return null;
  return (
    <div className={`mb-1.5 flex flex-col gap-1 ${className}`}>
      {quotes.map((quote, index) => (
        <blockquote key={index} className="line-clamp-4 whitespace-pre-wrap break-words border-l-2 border-accent-ink pl-2.5 text-[13px] leading-snug text-fg-dim">
          {quote}
        </blockquote>
      ))}
    </div>
  );
}
