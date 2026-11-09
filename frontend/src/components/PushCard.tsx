import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import {
  currentEndpoint, disablePush, enablePush, getPushState, pushSupported, removePushDevice, sendTestPush,
  type PushState,
} from '@/lib/push';
import { Toggle } from '@/pages/Notifications';

/** Notifications › Push: reach this device when Cognitrix isn't open. */
export function PushCard() {
  const [state, setState] = useState<PushState | null>(null);
  const [here, setHere] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [next, endpoint] = await Promise.all([getPushState(), currentEndpoint().catch(() => null)]);
      setState(next);
      setHere(endpoint);
    } catch {
      setState({ available: false, public_key: '', subscriptions: [] });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (!state?.available) return null;

  const supported = pushSupported();
  const onHere = Boolean(here && state.subscriptions.some((s) => s.endpoint === here));
  const others = state.subscriptions.filter((s) => s.endpoint !== here);

  const run = async (work: () => Promise<void>, done?: string) => {
    setBusy(true);
    setNote(null);
    try {
      await work();
      if (done) setNote({ ok: true, text: done });
    } catch (err) {
      setNote({ ok: false, text: errorMessage(err, err instanceof Error ? err.message : 'That did not work.') });
    } finally {
      await load();
      setBusy(false);
    }
  };

  return (
    <div className="mt-5 rounded-[14px] border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
        <div className="min-w-0 flex-1 basis-[200px]">
          <h2 className="text-[13.5px] font-medium">Push to this device</h2>
          <p className="mt-0.5 text-[12px] leading-snug text-fg-dim">
            {supported
              ? 'Approvals, agent alerts and task results reach you here even when Cognitrix is closed.'
              : "This browser can't receive push. On an iPhone or iPad, add Cognitrix to the Home Screen and open it from there."}
          </p>
        </div>
        {onHere && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void run(sendTestPush, 'Sent. It should appear in a moment.')}
            className="h-9 rounded-lg px-2.5 text-[12.5px] text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-60 md:h-8"
          >
            Send test
          </button>
        )}
        <Toggle
          label="Push to this device"
          checked={onHere}
          disabled={busy || !supported}
          onChange={(on) => void run(async () => {
            if (on) await enablePush(state.public_key);
            else await disablePush();
          })}
        />
      </div>
      {note && <p role={note.ok ? 'status' : 'alert'} className={`px-4 pb-3 text-[12.5px] ${note.ok ? 'text-fg-dim' : 'text-danger-ink'}`}>{note.text}</p>}
      {others.length > 0 && (
        <ul className="border-t border-line">
          {others.map((device) => (
            <li key={device.id} className="flex items-center gap-3 border-b border-line px-4 py-2.5 text-[13px] last:border-b-0">
              <span className="min-w-0 flex-1 truncate">{device.label}</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => void run(() => removePushDevice(device.id))}
                className="text-[12.5px] text-fg-dim hover:text-fg disabled:opacity-60"
              >
                Turn off
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
