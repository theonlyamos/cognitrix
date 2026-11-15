import { useState } from 'react';
import { Bot, Database, Download, LayoutPanelTop, Plug } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { POWER_COPY, type PageDetail, type PagePower } from '@/lib/artifactPages';
import { tr } from '@/lib/i18n';

const ICONS: Record<PagePower, JSX.Element> = {
  apps: <Plug size={15} />, ask: <Bot size={15} />, data: <Database size={15} />, download: <Download size={15} />,
};

/** Asked once per viewer: what an artifact page may do for them. */
export function ConsentDialog({ page, onDone }: { page: PageDetail; onDone: () => void }) {
  const [picked, setPicked] = useState<PagePower[]>(page.powers);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const answer = async (allowed: PagePower[]) => {
    setBusy(true);
    setError('');
    try {
      await api.put(`/artifact-pages/${page.id}/consent`, { allowed });
      onDone();
    } catch (e) {
      setError(errorMessage(e, tr('ui.artifacts.consentDialog.couldNotSaveYourAnswer')));
      setBusy(false);
    }
  };

  return (
    <div className="absolute inset-0 z-30 flex items-end justify-center bg-black/45 md:items-center">
      <div role="dialog" aria-modal="true" aria-label={tr('ui.artifacts.consentDialog.thisPageWantsToUse')}
        className="flex max-h-full w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl md:w-[480px] md:rounded-2xl">
        <header className="flex flex-none items-center gap-3 border-b border-line px-4 py-3.5">
          <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-accent/15 text-accent-ink"><LayoutPanelTop size={17} /></span>
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold">{tr('ui.artifacts.consentDialog.thisPageWantsToUse')}</span>
            <span className="block truncate text-[12.5px] text-fg-dim">{page.title}{page.agent?.name ? tr('ui.artifacts.consentDialog.madeByName', { name: page.agent.name }) : ''}</span>
          </span>
        </header>
        <ul className="min-h-0 flex-1 overflow-y-auto px-4">
          {page.powers.map((power) => (
            <li key={power} className="border-b border-line last:border-b-0">
              <label className="flex cursor-pointer items-start gap-3 py-3">
                <span className="grid h-8 w-8 flex-none place-items-center rounded-lg border border-line bg-bg text-fg-dim">{ICONS[power]}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-medium">{POWER_COPY[power].title}</span>
                  <span className="mt-0.5 block text-[12.5px] leading-snug text-fg-dim">{POWER_COPY[power].detail}</span>
                </span>
                <input type="checkbox" className="mt-1 h-4 w-4 accent-accent-ink" checked={picked.includes(power)}
                  onChange={(e) => setPicked((list) => (e.target.checked ? [...list, power] : list.filter((p) => p !== power)))} />
              </label>
            </li>
          ))}
        </ul>
        {error && <p role="alert" className="px-4 pb-2 text-[12.5px] text-danger-ink">{error}</p>}
        <footer className="flex flex-none items-center gap-2 border-t border-line px-4 py-3">
          <span className="min-w-0 flex-1 text-[12px] text-fg-dim">{tr('ui.artifacts.consentDialog.changeThisLaterFromThe')}</span>
          <button type="button" disabled={busy} onClick={() => void answer([])}
            className="inline-flex h-9 items-center rounded-md border border-line px-3 text-[13px] hover:bg-panel-2 md:h-8">{tr('ui.artifacts.consentDialog.notNow')}</button>
          <button type="button" disabled={busy} onClick={() => void answer(picked)}
            className="inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-[13px] font-semibold text-accent-foreground md:h-8">{tr('ui.artifacts.consentDialog.allow')}</button>
        </footer>
      </div>
    </div>
  );
}
