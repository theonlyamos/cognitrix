import { useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { deviceScreen, type PairedDevice } from '@/lib/devices';
import { quietButtonClass } from '@/components/settings/ui';
import { tr } from '@/lib/i18n';
import { cn } from '@/lib/utils';

/** A paired PC's screen, refreshed every few seconds while it's shown. */
export function PcScreen({ device, every = 2500, className }: { device: PairedDevice; every?: number; className?: string }) {
  const [image, setImage] = useState<string | null>(null);
  const [error, setError] = useState('');
  const allowed = device.powers.includes('apps');
  const live = allowed && device.online && !device.paused;

  useEffect(() => {
    if (!live) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      deviceScreen(device.id)
        .then((s) => { if (alive) { setImage(s.image); setError(''); } })
        .catch((err) => { if (alive) setError(errorMessage(err, tr('ui.pcScreen.couldNotSeeTheScreen'))); })
        .finally(() => { if (alive && !document.hidden) timer = setTimeout(tick, every); else if (alive) timer = setTimeout(tick, every * 4); });
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [device.id, live, every]);

  const note = !allowed
    ? tr('ui.pcScreen.nameDoesnTShareItsScreen', { name: device.name })
    : !device.online
      ? tr('ui.pcScreen.nameIsOffline', { name: device.name })
      : device.paused
        ? tr('ui.pcScreen.agentsArePausedOnName', { name: device.name })
        : error;

  return (
    <div className={cn('relative overflow-hidden rounded-lg border border-line bg-panel-2', className)}>
      {image && live ? (
        <img src={image} alt={tr('ui.pcScreen.nameSScreen', { name: device.name })} className={cn('block h-auto w-full', error && 'opacity-60')} />
      ) : (
        <div className="grid aspect-video place-items-center px-4 text-center text-[12.5px] text-fg-dim">
          {note || tr('ui.pcScreen.loadingTheScreen')}
        </div>
      )}
      {image && live && error && (
        <div role="status" className="absolute inset-x-0 bottom-0 bg-black/60 px-2 py-1 text-[11.5px] text-white">{error}</div>
      )}
    </div>
  );
}

/** The chat's right side when it works on a paired PC: its screen, larger. */
export function PcScreenPanel({ device, onClose, onBack }: { device: PairedDevice; onClose: () => void; onBack?: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex h-14 flex-none items-center gap-2 border-b border-line px-3">
        {onBack && <button type="button" onClick={onBack} className={quietButtonClass}>{tr('ui.pcScreen.back')}</button>}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-medium">{device.name}</div>
          <div className="text-[11.5px] text-fg-dim">{tr('ui.pcScreen.yourPcAgentsWorkIn')}</div>
        </div>
        <button type="button" onClick={onClose} aria-label={tr('ui.pcScreen.close')} className={quietButtonClass}>✕</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        <PcScreen device={device} every={1500} />
        <p className="mt-2 text-[12px] leading-snug text-fg-dim">{tr('ui.pcScreen.agentsWorkInAppWindows')}</p>
      </div>
    </div>
  );
}
