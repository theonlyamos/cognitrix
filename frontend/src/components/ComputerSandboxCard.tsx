import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { getSandboxSettings, setSandboxEnabled, type SandboxSettings } from '@/lib/sandbox';
import { SettingRow, StatusPill } from '@/components/settings/ui';
import { Toggle } from '@/pages/Notifications';
import { SandboxEnvCard } from '@/components/SandboxEnvCard';
import { SandboxSnapshotsCard } from '@/components/SandboxSnapshotsCard';
import { tr } from '@/lib/i18n';

/** Settings › Agent access: give agents their own sandboxed computer. */
export function ComputerSandboxCard() {
  const [settings, setSettings] = useState<SandboxSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setSettings(await getSandboxSettings());
    } catch (err) {
      setError(errorMessage(err, tr('ui.computerSandboxCard.couldnTLoadThisSetting')));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function change(enabled: boolean) {
    setBusy(true);
    setError('');
    try {
      setSettings(await setSandboxEnabled(enabled));
    } catch (err) {
      setError(errorMessage(err, tr('ui.computerSandboxCard.couldnTChangeThisSetting')));
    } finally {
      setBusy(false);
    }
  }

  const docker = settings?.docker;
  const fly = settings?.host === 'fly' ? settings.fly : undefined;
  const dockerNote = fly
    ? fly.configured
      ? <StatusPill tone="ok">{tr('ui.computerSandboxCard.onFly')}</StatusPill>
      : <StatusPill tone="warn">{tr('ui.computerSandboxCard.flySettingsMissing')}</StatusPill>
    : !docker
    ? null
    : !docker.installed
      ? <StatusPill tone="warn">{tr('ui.computerSandboxCard.dockerNotInstalled')}</StatusPill>
      : !docker.running
        ? <StatusPill tone="warn">{tr('ui.computerSandboxCard.dockerNotRunning')}</StatusPill>
        : docker.image
          ? <StatusPill tone="ok">{tr('ui.computerSandboxCard.ready')}</StatusPill>
          : <StatusPill tone="off">{tr('ui.computerSandboxCard.downloadsOnFirstUse')}</StatusPill>;

  return (
    <div>
      <SettingRow
        className="px-0 py-1"
        label={tr('ui.computerSandboxCard.giveAgentsTheirOwnComputer')}
        detail={
          <>
            {fly ? (
              <>
                {tr('ui.computerSandboxCard.eachPersonGetsTheirOwn')}{fly.app ? tr('ui.computerSandboxCard.appAppValue', { app: fly.app, value: fly.region ? `, ${fly.region}` : '' }) : ''}{tr('ui.computerSandboxCard.withADesktopPerAgent')}{' '}<span className="font-medium">{tr('ui.computerSandboxCard.computer')}</span>{' '}{tr('ui.computerSandboxCard.inChatValueValue2', { value: fly.idle_minutes > 0 && tr('ui.computerSandboxCard.itValueAfterIdleMinutes', { value: fly.idle_action === 'suspend' ? 'is suspended' : 'stops', idle_minutes: fly.idle_minutes }), value2: !fly.configured && tr('ui.computerSandboxCard.theServerStillNeedsMissing', { missing: fly.missing.join(', ') }) })}
              </>
            ) : (
              <>
                {tr('ui.computerSandboxCard.agentsWorkOnALinux')}{' '}<span className="font-medium">{tr('ui.computerSandboxCard.computer')}</span>{' '}{tr('ui.computerSandboxCard.inChatNeeds')}{' '}
                <a className="text-accent-ink hover:underline" href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noreferrer">{tr('ui.computerSandboxCard.dockerDesktop')}</a>.
              </>
            )}
            {settings?.from_env && tr('ui.computerSandboxCard.setByTheServerCognitrix')}
          </>
        }
      >
        {dockerNote}
        <Toggle
          label={tr('ui.computerSandboxCard.giveAgentsTheirOwnComputer')}
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
      {settings?.enabled && (
        <div className="mt-3 border-t border-line pt-3">
          <SandboxSnapshotsCard />
        </div>
      )}
    </div>
  );
}
