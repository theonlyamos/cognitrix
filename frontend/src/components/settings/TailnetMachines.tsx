import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { errorMessage } from '@/lib/api';
import { tr } from '@/lib/i18n';
import {
  allowTailnetDevice, checkTailnetComputers, removeTailnetDevice, tailnetDevices,
  type TailnetCheckStep, type TailnetComputers, type TailnetDevice, type TailnetDevices, type TailnetScope,
  type TailscaleChange,
} from '@/lib/tailscale';
import {
  Group, SettingRow, StatusPill, SubHeading, buttonClass, primaryButtonClass, quietButtonClass,
} from '@/components/settings/ui';

const OAUTH_URL = 'https://console.tailscale.com/admin/settings/trust-credentials';
const POLICY_URL = 'https://login.tailscale.com/admin/acls/file';

const fieldClass = 'h-9 w-full min-w-0 rounded-lg border border-line bg-bg px-2.5 text-[13px] outline-none focus:border-accent disabled:opacity-60 md:h-8';

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{children}</a>;
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };
  return (
    <button type="button" onClick={copy} aria-label={label} className={buttonClass}>
      {copied ? tr('ui.tailscale.copied') : tr('ui.tailscale.copy')}
    </button>
  );
}

function stepText(step: TailnetCheckStep): string {
  if (step.name === 'oauth') return step.ok ? tr('ui.tailnetMachines.checkOauthOk') : tr('ui.tailnetMachines.checkOauthBad');
  if (step.name === 'join_key') return step.ok ? tr('ui.tailnetMachines.checkJoinKeyOk') : tr('ui.tailnetMachines.checkJoinKeyBad');
  return step.ok ? tr('ui.tailnetMachines.checkEngineOk') : tr('ui.tailnetMachines.checkEngineBad');
}

/** Settings › Tailscale: the agents' computers on Fly join the tailnet. */
export function TailnetComputersCard({ computers, locked, onSave }: {
  computers: TailnetComputers;
  locked: boolean;
  onSave: (change: TailscaleChange) => Promise<void>;
}) {
  const [id, setId] = useState(computers.oauth_id);
  const [secret, setSecret] = useState('');
  const [steps, setSteps] = useState<TailnetCheckStep[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setId(computers.oauth_id); }, [computers.oauth_id]);

  const on = computers.scope !== 'nobody';
  const saveClient = async () => {
    setSteps(null);
    await onSave({ oauth_id: id.trim(), ...(secret.trim() ? { oauth_secret: secret.trim() } : {}) });
    setSecret('');
  };
  const forgetClient = async () => {
    setSteps(null);
    await onSave({ oauth_id: '', oauth_secret: '' });
  };
  const check = async () => {
    setChecking(true);
    setError('');
    try {
      setSteps((await checkTailnetComputers()).steps);
    } catch (err) {
      setError(errorMessage(err, tr('ui.tailscale.couldNotCheck')));
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
      <SubHeading>{tr('ui.tailnetMachines.computersHeading')}</SubHeading>
      <Group>
        <SettingRow label={tr('ui.tailnetMachines.whoseComputersJoin')} detail={tr('ui.tailnetMachines.whoseComputersJoinDetail')}>
          <select
            value={computers.scope}
            disabled={locked}
            aria-label={tr('ui.tailnetMachines.whoseComputersJoin')}
            onChange={(e) => { setSteps(null); void onSave({ sandbox: e.target.value as TailnetComputers['scope'] }); }}
            className={`${fieldClass} w-auto`}
          >
            <option value="nobody">{tr('ui.tailnetMachines.nobodys')}</option>
            <option value="admins">{tr('ui.tailnetMachines.adminsComputers')}</option>
            <option value="everyone">{tr('ui.tailnetMachines.everyonesComputers')}</option>
          </select>
        </SettingRow>
        {on && (
          <>
            <div className="px-3.5 py-3">
              <p className="text-[13px] font-medium">{tr('ui.tailnetMachines.oauthClient')}</p>
              <p className="mt-0.5 max-w-[62ch] text-[12px] leading-snug text-fg-dim">
                {tr('ui.tailnetMachines.oauthClientDetail', { tag: computers.tag })}{' '}
                <ExternalLink href={OAUTH_URL}>{tr('ui.tailnetMachines.makeOneAtTailscale')}</ExternalLink>
              </p>
              <form
                className="mt-2 flex flex-wrap items-center gap-2"
                onSubmit={(e) => { e.preventDefault(); void saveClient(); }}
              >
                <input
                  value={id}
                  onChange={(e) => setId(e.target.value)}
                  autoComplete="off"
                  disabled={locked}
                  aria-label={tr('ui.tailnetMachines.clientId')}
                  placeholder={tr('ui.tailnetMachines.clientId')}
                  className={`${fieldClass} flex-1 basis-[160px]`}
                />
                <input
                  type="password"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  autoComplete="off"
                  disabled={locked}
                  aria-label={tr('ui.tailnetMachines.clientSecret')}
                  placeholder={computers.oauth_saved ? tr('ui.tailnetMachines.secretSaved') : tr('ui.tailnetMachines.clientSecret')}
                  className={`${fieldClass} flex-1 basis-[200px]`}
                />
                <button
                  type="submit"
                  disabled={locked || !id.trim() || (!secret.trim() && (!computers.oauth_saved || id.trim() === computers.oauth_id))}
                  className={primaryButtonClass}
                >
                  {tr('ui.tailnetMachines.save')}
                </button>
                {computers.oauth_saved && (
                  <button type="button" disabled={locked} onClick={() => void forgetClient()} className={quietButtonClass}>
                    {tr('ui.tailnetMachines.forget')}
                  </button>
                )}
              </form>
            </div>
            <SettingRow label={tr('ui.tailnetMachines.computersTag')} detail={tr('ui.tailnetMachines.computersTagDetail')}>
              <input
                key={computers.tag}
                defaultValue={computers.tag}
                maxLength={80}
                disabled={locked}
                aria-label={tr('ui.tailnetMachines.computersTag')}
                onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== computers.tag) void onSave({ sandbox_tag: v }); }}
                className={`${fieldClass} w-[220px] font-mono text-[12px]`}
              />
            </SettingRow>
            <div className="px-3.5 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <p className="mr-auto text-[13px] font-medium">{tr('ui.tailnetMachines.policyHeading')}</p>
                <CopyButton text={computers.policy} label={tr('ui.tailnetMachines.copyThePolicyLines')} />
              </div>
              <p className="mt-0.5 max-w-[62ch] text-[12px] leading-snug text-fg-dim">
                {tr('ui.tailnetMachines.policyDetail')}{' '}
                <ExternalLink href={POLICY_URL}>{tr('ui.tailnetMachines.openThePolicyFile')}</ExternalLink>
              </p>
              <pre className="mt-2 max-h-[240px] overflow-auto rounded-lg bg-bg p-2.5 font-mono text-[11.5px] leading-relaxed">{computers.policy}</pre>
            </div>
            <div className="px-3.5 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" disabled={checking || !computers.oauth_saved} onClick={() => void check()} className={buttonClass}>
                  {checking ? tr('ui.tailscale.checking') : tr('ui.tailnetMachines.checkTheConnection')}
                </button>
                {error && <span role="alert" className="text-[12.5px] text-danger-ink">{error}</span>}
              </div>
              {steps && (
                <ul className="mt-2 space-y-1.5" aria-label={tr('ui.tailnetMachines.checkResults')}>
                  {steps.map((step) => (
                    <li key={step.name} className="flex flex-wrap items-baseline gap-2 text-[12.5px] leading-snug">
                      <StatusPill tone={step.ok ? 'ok' : 'warn'}>{step.ok ? tr('ui.tailnetMachines.ok') : tr('ui.tailnetMachines.notYet')}</StatusPill>
                      <span>{stepText(step)}</span>
                      {!step.ok && step.detail && <span className="text-fg-dim">{step.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="px-3.5 py-3 text-[12px] leading-snug text-fg-dim">
              {computers.on_fly ? tr('ui.tailnetMachines.onFlyNote') : tr('ui.tailnetMachines.notOnFlyNote')}{' '}
              {tr('ui.tailnetMachines.noAskingInTheComputer')}
            </p>
          </>
        )}
      </Group>
    </>
  );
}

function names(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).map((n) => n.trim()).filter(Boolean))];
}

function DeviceRow({ device, locked, onChanged, onError }: {
  device: TailnetDevice;
  locked: boolean;
  onChanged: () => void;
  onError: (m: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [users, setUsers] = useState(device.users.join(', '));
  const [path, setPath] = useState<TailnetDevice['path']>(device.allowed ? device.path : (device.tailscale_ssh ? 'tailscale' : 'key'));
  const [busy, setBusy] = useState(false);

  const run = async (work: () => Promise<unknown>, failed: string) => {
    setBusy(true);
    onError('');
    try {
      await work();
      setEditing(false);
      onChanged();
    } catch (err) {
      onError(errorMessage(err, failed));
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(() => allowTailnetDevice(device.node, names(users), path), tr('ui.tailnetMachines.couldNotSaveDevice'));
  const remove = () => run(() => removeTailnetDevice(device.node), tr('ui.tailnetMachines.couldNotRemoveDevice'));

  const how = device.path === 'key' ? tr('ui.tailnetMachines.thisServersKey') : tr('ui.tailnetMachines.tailscaleSsh');
  let detail: string;
  if (!device.present) detail = tr('ui.tailnetMachines.noLongerOnYourTailnet');
  else if (!device.allowed) detail = tr('ui.tailnetMachines.agentsCantUseIt');
  else if (!device.users.length) detail = tr('ui.tailnetMachines.noLoginNamesYet');
  else detail = tr('ui.tailnetMachines.runsAs', { users: device.users.join(', '), how });

  return (
    <li className="px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 basis-[180px]">
          <p className="flex flex-wrap items-center gap-2 text-[13px] font-medium">
            <span className="break-all">{device.name}</span>
            {device.os && <span className="font-mono text-[11px] font-normal text-fg-dim">{device.os}</span>}
            {device.present && (
              <StatusPill tone={device.online ? 'ok' : 'off'}>{device.online ? tr('ui.tailnetMachines.online') : tr('ui.tailnetMachines.offline')}</StatusPill>
            )}
          </p>
          <p className="mt-0.5 text-[12px] leading-snug text-fg-dim">{detail}</p>
        </div>
        {!editing && device.present && (
          <button
            type="button"
            disabled={locked || busy}
            onClick={() => setEditing(true)}
            aria-label={device.allowed ? tr('ui.tailnetMachines.changeNamed', { name: device.name }) : tr('ui.tailnetMachines.allowNamed', { name: device.name })}
            className={buttonClass}
          >
            {device.allowed ? tr('ui.tailnetMachines.change') : tr('ui.tailnetMachines.allow')}
          </button>
        )}
        {!editing && device.allowed && (
          <button
            type="button"
            disabled={locked || busy}
            onClick={() => void remove()}
            aria-label={tr('ui.tailnetMachines.removeNamed', { name: device.name })}
            className={quietButtonClass}
          >
            {tr('ui.tailnetMachines.remove')}
          </button>
        )}
      </div>
      {editing && (
        <form className="mt-2.5 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <label className="min-w-0 flex-1 basis-[200px] text-[12px] text-fg-dim">
            {tr('ui.tailnetMachines.loginNames')}
            <input
              value={users}
              onChange={(e) => setUsers(e.target.value)}
              autoComplete="off"
              autoFocus
              placeholder={tr('ui.tailnetMachines.loginNamesPlaceholder')}
              className={`${fieldClass} mt-1 text-fg`}
            />
          </label>
          <label className="text-[12px] text-fg-dim">
            {tr('ui.tailnetMachines.howItSignsIn')}
            <select
              value={path}
              onChange={(e) => setPath(e.target.value as TailnetDevice['path'])}
              className={`${fieldClass} mt-1 block w-auto text-fg`}
            >
              <option value="tailscale">{tr('ui.tailnetMachines.tailscaleSsh')}</option>
              <option value="key">{tr('ui.tailnetMachines.thisServersKey')}</option>
            </select>
          </label>
          <button type="submit" disabled={busy || !names(users).length} className={primaryButtonClass}>{tr('ui.tailnetMachines.save')}</button>
          <button type="button" disabled={busy} onClick={() => setEditing(false)} className={quietButtonClass}>{tr('ui.tailnetMachines.cancel')}</button>
          {path === 'tailscale' && !device.tailscale_ssh && (
            <p className="basis-full text-[12px] leading-snug text-warn-ink">{tr('ui.tailnetMachines.noTailscaleSshThere')}</p>
          )}
        </form>
      )}
    </li>
  );
}

/** Settings › Tailscale: the tailnet machines agents may run commands on. */
export function TailnetDevicesCard({ scope, locked, onSave }: {
  scope: TailnetScope;
  locked: boolean;
  onSave: (change: TailscaleChange) => Promise<void>;
}) {
  const [found, setFound] = useState<TailnetDevices | null>(null);
  const [error, setError] = useState('');
  const on = scope !== 'off';

  const load = useCallback(() => {
    tailnetDevices().then(setFound)
      .catch((err) => setError(errorMessage(err, tr('ui.tailnetMachines.couldNotLoadDevices'))));
  }, []);
  useEffect(() => { if (on) load(); }, [on, load]);

  const usesKey = !!found?.devices.some((d) => d.allowed && d.path === 'key');

  return (
    <>
      <SubHeading>{tr('ui.tailnetMachines.devicesHeading')}</SubHeading>
      <Group>
        <SettingRow label={tr('ui.tailnetMachines.whoseAgentsMayUseThem')} detail={tr('ui.tailnetMachines.whoseAgentsMayUseThemDetail')}>
          <select
            value={scope}
            disabled={locked}
            aria-label={tr('ui.tailnetMachines.whoseAgentsMayUseThem')}
            onChange={(e) => void onSave({ devices: e.target.value as TailnetScope })}
            className={`${fieldClass} w-auto`}
          >
            <option value="off">{tr('ui.tailnetMachines.nobodysAgents')}</option>
            <option value="admins">{tr('ui.tailnetMachines.adminsAgents')}</option>
            <option value="everyone">{tr('ui.tailnetMachines.everyonesAgents')}</option>
          </select>
        </SettingRow>
        {on && error && <p role="alert" className="px-3.5 py-3 text-[12.5px] text-danger-ink">{error}</p>}
        {on && found && !found.ssh && (
          <p role="status" className="px-3.5 py-3 text-[12.5px] leading-snug text-warn-ink">{tr('ui.tailnetMachines.noSshProgram')}</p>
        )}
        {on && found && (found.devices.length ? (
          <ul aria-label={tr('ui.tailnetMachines.devicesHeading')} className="divide-y divide-line">
            {found.devices.map((device) => (
              <DeviceRow key={device.node} device={device} locked={locked} onChanged={load} onError={setError} />
            ))}
          </ul>
        ) : (
          <p className="px-3.5 py-3 text-[12.5px] text-fg-dim">{tr('ui.tailnetMachines.noOtherMachines')}</p>
        ))}
        {on && found?.key && (
          <details className="px-3.5 py-3" open={usesKey}>
            <summary className="cursor-pointer text-[13px] font-medium">{tr('ui.tailnetMachines.thisServersKey')}</summary>
            <p className="mt-1 max-w-[62ch] text-[12px] leading-snug text-fg-dim">{tr('ui.tailnetMachines.thisServersKeyDetail')}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 basis-[220px] break-all rounded bg-bg px-2 py-1.5 font-mono text-[11.5px]">{found.key}</code>
              <CopyButton text={found.key} label={tr('ui.tailnetMachines.copyTheKey')} />
            </div>
          </details>
        )}
        {on && (
          <p className="px-3.5 py-3 text-[12px] leading-snug text-fg-dim">{tr('ui.tailnetMachines.giveAgentsTheTools')}</p>
        )}
      </Group>
    </>
  );
}
