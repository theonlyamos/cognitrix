import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { getSandboxSettings, setSandboxEnabled, type SandboxSettings } from '@/lib/sandbox';
import { SettingRow, StatusPill } from '@/components/settings/ui';
import { Toggle } from '@/pages/Notifications';
import { SandboxEnvCard } from '@/components/SandboxEnvCard';

/** Settings › Agent access: give agents their own sandboxed computer. */
export function ComputerSandboxCard() {
  const [settings, setSettings] = useState<SandboxSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setSettings(await getSandboxSettings());
    } catch (err) {
      setError(errorMessage(err, "Couldn't load this setting."));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function change(enabled: boolean) {
    setBusy(true);
    setError('');
    try {
      setSettings(await setSandboxEnabled(enabled));
    } catch (err) {
      setError(errorMessage(err, "Couldn't change this setting."));
    } finally {
      setBusy(false);
    }
  }

  const docker = settings?.docker;
  const fly = settings?.host === 'fly' ? settings.fly : undefined;
  const dockerNote = fly
    ? fly.configured
      ? <StatusPill tone="ok">on Fly</StatusPill>
      : <StatusPill tone="warn">Fly settings missing</StatusPill>
    : !docker
    ? null
    : !docker.installed
      ? <StatusPill tone="warn">Docker not installed</StatusPill>
      : !docker.running
        ? <StatusPill tone="warn">Docker not running</StatusPill>
        : docker.image
          ? <StatusPill tone="ok">ready</StatusPill>
          : <StatusPill tone="off">downloads on first use</StatusPill>;

  return (
    <div>
      <SettingRow
        className="px-0 py-1"
        label="Give agents their own computer"
        detail={
          <>
            {fly ? (
              <>
                Each person gets their own Linux computer on Fly{fly.app ? ` (app ${fly.app}${fly.region ? `, ${fly.region}` : ''})` : ''}, with a desktop per agent. Watch and take over from <span className="font-medium">Computer</span> in chat.
                {fly.idle_minutes > 0 && ` It ${fly.idle_action === 'suspend' ? 'is suspended' : 'stops'} after ${fly.idle_minutes} idle minutes and wakes on the next request; files and logins are kept.`}
                {!fly.configured && ` The server still needs: ${fly.missing.join(', ')}.`}
              </>
            ) : (
              <>
                Agents work on a Linux desktop in a container instead of this computer's screen, each agent on its own desktop.
                Watch and take over from <span className="font-medium">Computer</span> in chat. Needs{' '}
                <a className="text-accent-ink hover:underline" href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noreferrer">Docker Desktop</a>.
              </>
            )}
            {settings?.from_env && ' Set by the server (COGNITRIX_COMPUTER_SANDBOX).'}
          </>
        }
      >
        {dockerNote}
        <Toggle
          label="Give agents their own computer"
          checked={!!settings?.enabled}
          onChange={(v) => void change(v)}
          disabled={!settings || busy || settings.from_env}
        />
      </SettingRow>
      {error && <p role="alert" className="mt-1 text-[12px] text-danger-ink">{error}</p>}
      {settings?.enabled && (
        <div className="mt-3 border-t border-line pt-3">
          <SandboxEnvCard />
        </div>
      )}
    </div>
  );
}
