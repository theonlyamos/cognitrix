import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

export type TurnDirection = -1 | 1;

/** How far past the end of an answer you scroll before the next turn comes. */
const PULL_SHORT = 60;
const PULL_LONG = 180;
const TOUCH_SHORT = 70;
const TOUCH_LONG = 130;
/** After a move, trackpad momentum is ignored for this long. */
const SETTLE_MS = 550;
/** A pull that stops for this long starts again from nothing. */
const PULL_RESET_MS = 650;

interface Options {
  /** The scrolling answer. */
  scroller: RefObject<HTMLElement | null>;
  /** Off while something else fills the stage. */
  enabled: boolean;
  hasPrev: boolean;
  hasNext: boolean;
  onMove: (direction: TurnDirection) => void;
}

const typingIn = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== 'function') return false;
  return !!el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="dialog"], [role="menu"]');
};

/**
 * Scroll, swipe and keys move one turn at a time. A long answer scrolls as
 * usual first; at its end you keep scrolling until a small bar fills, so a
 * quick flick never skips past turns.
 */
export function useTurnPaging({ scroller, enabled, hasPrev, hasNext, onMove }: Options) {
  const [pull, setPull] = useState<{ direction: TurnDirection; progress: number } | null>(null);
  const state = useRef({ amount: 0, direction: 0 as 0 | TurnDirection, settleUntil: 0, resetTimer: 0 as ReturnType<typeof setTimeout> | 0 });
  const latest = useRef({ hasPrev, hasNext, onMove, enabled });
  latest.current = { hasPrev, hasNext, onMove, enabled };

  const clear = useCallback(() => {
    const s = state.current;
    s.amount = 0;
    s.direction = 0;
    if (s.resetTimer) clearTimeout(s.resetTimer);
    s.resetTimer = 0;
    setPull(null);
  }, []);

  const move = useCallback((direction: TurnDirection) => {
    clear();
    state.current.settleUntil = Date.now() + SETTLE_MS;
    latest.current.onMove(direction);
  }, [clear]);

  useEffect(() => {
    const el = scroller.current;
    if (!el || !enabled) return undefined;
    const fits = () => el.scrollHeight <= el.clientHeight + 2;
    const atEdge = (direction: TurnDirection) => (direction > 0
      ? el.scrollTop + el.clientHeight >= el.scrollHeight - 2
      : el.scrollTop <= 1);
    const canGo = (direction: TurnDirection) => (direction > 0 ? latest.current.hasNext : latest.current.hasPrev);

    const add = (direction: TurnDirection, amount: number, need: number) => {
      const s = state.current;
      if (s.direction !== direction) { s.amount = 0; s.direction = direction; }
      s.amount += amount;
      if (s.resetTimer) clearTimeout(s.resetTimer);
      s.resetTimer = setTimeout(clear, PULL_RESET_MS);
      if (s.amount >= need) move(direction);
      else setPull({ direction, progress: s.amount / need });
    };

    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.deltaY === 0) return;
      const direction: TurnDirection = event.deltaY > 0 ? 1 : -1;
      if (!atEdge(direction) || !canGo(direction)) { if (state.current.amount) clear(); return; }
      event.preventDefault();
      if (Date.now() < state.current.settleUntil) return;
      const lines = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientHeight : 1;
      add(direction, Math.min(Math.abs(event.deltaY) * lines, 120), fits() ? PULL_SHORT : PULL_LONG);
    };

    let touch: { y: number; top: boolean; bottom: boolean } | null = null;
    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) { touch = null; return; }
      touch = { y: event.touches[0].clientY, top: atEdge(-1), bottom: atEdge(1) };
    };
    const onTouchMove = (event: TouchEvent) => {
      if (!touch) return;
      const dy = touch.y - event.touches[0].clientY;
      const direction: TurnDirection = dy > 0 ? 1 : -1;
      const started = direction > 0 ? touch.bottom : touch.top;
      if (!started || !canGo(direction)) return;
      const need = fits() ? TOUCH_SHORT : TOUCH_LONG;
      state.current.direction = direction;
      state.current.amount = Math.abs(dy);
      setPull({ direction, progress: Math.min(1, Math.abs(dy) / need) });
    };
    const onTouchEnd = () => {
      const s = state.current;
      const need = fits() ? TOUCH_SHORT : TOUCH_LONG;
      const direction = s.direction;
      const done = touch && direction && s.amount >= need;
      touch = null;
      if (done) move(direction as TurnDirection);
      else clear();
    };

    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMove, { passive: true });
    el.addEventListener('touchend', onTouchEnd);
    el.addEventListener('touchcancel', onTouchEnd);
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove', onTouchMove);
      el.removeEventListener('touchend', onTouchEnd);
      el.removeEventListener('touchcancel', onTouchEnd);
      clear();
    };
  }, [scroller, enabled, clear, move]);

  // Page Up / Page Down, the arrow keys outside the box, and Alt + arrows anywhere.
  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const el = scroller.current;
      if (!el) return;
      const page = event.key === 'PageDown' ? 1 : event.key === 'PageUp' ? -1 : 0;
      const arrow = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
      if (!page && !arrow) return;
      const direction = (page || arrow) as TurnDirection;
      const canGo = direction > 0 ? latest.current.hasNext : latest.current.hasPrev;
      if (event.altKey) {
        if (!arrow) return;
        event.preventDefault();
        if (canGo) move(direction);
        return;
      }
      if (typingIn(event.target)) return;
      const atEdge = direction > 0 ? el.scrollTop + el.clientHeight >= el.scrollHeight - 2 : el.scrollTop <= 1;
      event.preventDefault();
      if (!atEdge) {
        el.scrollBy({ top: direction * (page ? el.clientHeight * 0.85 : 80) });
        return;
      }
      if (canGo && Date.now() >= state.current.settleUntil) move(direction);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled, scroller, move]);

  return pull;
}
