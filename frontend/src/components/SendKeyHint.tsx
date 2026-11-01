import { useSendKey } from '@/lib/preferences';

/** "⏎ send · ⇧⏎ newline" under a composer, following Settings › Appearance. */
export function SendKeyHint() {
  const mode = useSendKey();
  const kbd = 'rounded border border-line px-1';
  return mode === 'enter' ? (
    <>
      <span className="ml-auto hidden sm:inline"><kbd className={kbd}>⏎</kbd> send</span>
      <span className="hidden sm:inline"><kbd className={kbd}>⇧⏎</kbd> newline</span>
    </>
  ) : (
    <>
      <span className="ml-auto hidden sm:inline"><kbd className={kbd}>Ctrl ⏎</kbd> send</span>
      <span className="hidden sm:inline"><kbd className={kbd}>⏎</kbd> newline</span>
    </>
  );
}
