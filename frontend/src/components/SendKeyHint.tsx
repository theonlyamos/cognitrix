import { useSendKey } from '@/lib/preferences';
import { tr } from '@/lib/i18n';

/** "⏎ send · ⇧⏎ newline" under a composer, following Settings › Appearance. */
export function SendKeyHint() {
  const mode = useSendKey();
  const kbd = 'rounded border border-line px-1';
  return mode === 'enter' ? (
    <>
      <span className="ml-auto hidden sm:inline"><kbd className={kbd}>⏎</kbd>{' '}{tr('ui.sendKeyHint.send')}</span>
      <span className="hidden sm:inline"><kbd className={kbd}>⇧⏎</kbd>{' '}{tr('ui.sendKeyHint.newline')}</span>
    </>
  ) : (
    <>
      <span className="ml-auto hidden sm:inline"><kbd className={kbd}>Ctrl ⏎</kbd>{' '}{tr('ui.sendKeyHint.send')}</span>
      <span className="hidden sm:inline"><kbd className={kbd}>⏎</kbd>{' '}{tr('ui.sendKeyHint.newline')}</span>
    </>
  );
}
