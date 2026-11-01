import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useUser } from '@/context/AppContext';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { desktop, isDesktop, type DesktopSettings } from '@/lib/desktop';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Toggle } from '@/pages/Notifications';
import { cn } from '@/lib/utils';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { VoiceSettingsCard } from '@/components/VoiceSettingsCard';
import { McpServersCard } from '@/components/McpServersCard';
import { ChatChannelsCard } from '@/components/ChatChannelsCard';

function Card({ title, children, className, id }: { title: string; children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} aria-label={title} className={cn('rounded-[14px] border border-line bg-panel p-4', className)}>
      <h2 className="mb-3 font-mono text-[10.5px] tracking-[0.14em] text-fg-dim">{title.toUpperCase()}</h2>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-[13px]">
      <span className="text-fg-dim">{label}</span>
      <span className="min-w-0 truncate text-right font-mono text-[12px]">{children}</span>
    </div>
  );
}

function SwitchRow({ label, detail, checked, onChange }: { label: string; detail?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0">
        <div className="text-[13.5px]">{label}</div>
        {detail && <div className="text-[12px] text-fg-dim">{detail}</div>}
      </div>
      <Toggle label={label} checked={checked} onChange={onChange} />
    </div>
  );
}

const onWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent);

const STATUS_TEXT = { starting: 'Starting', running: 'Running', stopped: 'Stopped', failed: 'Failed' } as const;

export default function Settings() {
  const desktopApp = isDesktop();
  const { user, logout } = useUser();
  const navigate = useNavigate();
  const engine = useOptionalNotifications()?.engine ?? null;
  const { info, reload } = useSystemInfo();
  const [prefs, setPrefs] = useState<DesktopSettings | null>(null);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  useEffect(() => {
    if (desktopApp) void desktop.getSettings().then(setPrefs);
  }, [desktopApp]);

  const updatePrefs = async (patch: Parameters<typeof desktop.setSettings>[0]) => {
    setPrefsError(null);
    try {
      const next = await desktop.setSettings(patch);
      if (next) setPrefs(next);
    } catch (err) {
      setPrefsError(String(err));
    }
  };

  const changeFolder = async () => {
    const folder = await desktop.pickWorkingFolder();
    if (folder) {
      setPrefs((p) => (p ? { ...p, working_folder: folder } : p));
      // The engine restarts to pick it up; ask again once it's back.
      setTimeout(reload, 4000);
    }
  };

  const signOutEverywhere = async () => {
    setSigningOut(true);
    setSignOutError(null);
    try {
      await api.post('/auth/logout');
      logout();
      navigate('/');
    } catch (err) {
      setSignOutError(errorMessage(err, 'Could not sign out of other devices.'));
      setSigningOut(false);
    }
  };

  const engineState = engine?.status ?? (info ? 'running' : 'starting');
  const engineAddress = engine?.url?.replace(/^https?:\/\//, '') ?? window.location.host;

  return (
    <div className="flex h-full min-w-0 flex-1 overflow-y-auto bg-bg text-fg">
      <div className="mx-auto w-full max-w-[1180px] px-7 py-6 max-md:px-4">
        <h1 className="text-[22px] font-semibold">Settings</h1>
        <p className="mt-1 text-[13.5px] text-fg-dim">
          {desktopApp ? 'This PC runs the engine, your agents and their tasks.' : 'Settings for this Cognitrix server and your account.'}
        </p>

        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <Card title="Local engine">
            <div className="mb-2 flex items-center gap-2">
              <span
                aria-hidden="true"
                className={cn('h-2 w-2 rounded-full', engineState === 'running' ? 'bg-ok' : engineState === 'starting' ? 'bg-[#ffb86b]' : 'bg-danger')}
              />
              <span className="flex-1 text-[14px] font-medium">{STATUS_TEXT[engineState]}</span>
              {desktopApp && (
                <>
                  <button type="button" onClick={() => desktop.openFolder('logs')} className="h-8 rounded-lg border border-line px-3 text-[12.5px] hover:bg-panel-2">
                    Open engine log
                  </button>
                  <button type="button" onClick={() => desktop.restartEngine()} className="h-8 rounded-lg border border-line px-3 text-[12.5px] hover:bg-panel-2">
                    Restart
                  </button>
                </>
              )}
            </div>
            {engine?.error && <p role="alert" className="mb-2 text-[12.5px] text-danger-ink">{engine.error}</p>}
            <Row label="Address">{engineAddress}</Row>
            <Row label="Version">{info?.version ?? '…'}</Row>
            <Row label="Database">{info ? (info.database === 'sqlite' ? 'SQLite' : info.database) : '…'}</Row>
            <Row label="Task queue">
              {info ? `${info.task_queue === 'filesystem' ? 'local queue' : 'Redis'}${info.worker_running == null ? '' : info.worker_running ? ' · worker running' : ' · worker idle'}` : '…'}
            </Row>
            <Row label="Data folder">
              {desktopApp ? (
                <button type="button" onClick={() => desktop.openFolder('data')} className="hover:underline">{info?.data_folder ?? prefs?.data_folder ?? '…'}</button>
              ) : (
                info?.data_folder ?? '…'
              )}
            </Row>
          </Card>

          <Card title="Working folder">
            <p className="text-[13px] leading-relaxed text-fg-dim">
              Agents' shell commands and file tools can only work inside this folder. Commands that point anywhere else are refused and the agent is told why.
            </p>
            <div className="mt-3 flex items-center gap-2 rounded-[10px] border border-line bg-bg px-3 py-2.5">
              <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{info?.working_folder ?? prefs?.working_folder ?? '…'}</span>
              {desktopApp && (
                <>
                  <button type="button" onClick={() => desktop.openFolder('working')} className="text-[12.5px] text-fg-dim hover:text-fg">Open</button>
                  <button type="button" onClick={changeFolder} className="text-[12.5px] font-medium text-accent-ink hover:underline">Change</button>
                </>
              )}
            </div>
            {!desktopApp && <p className="mt-2 text-[12px] text-fg-dim">Set with COGNITRIX_TOOLS_ROOT on the server.</p>}
          </Card>

          {desktopApp && prefs && (
            <Card title="Desktop">
              <SwitchRow label={onWindows ? 'Start with Windows' : 'Start at login'} detail="Opens in the tray when you sign in" checked={prefs.start_with_windows} onChange={(v) => updatePrefs({ start_with_windows: v })} />
              <SwitchRow label="Keep running in the tray when closed" detail="Tasks keep running while the window is closed" checked={prefs.close_to_tray} onChange={(v) => updatePrefs({ close_to_tray: v })} />
              <SwitchRow label={onWindows ? 'Windows notifications' : 'System notifications'} detail="Fine-tune each event under Notifications" checked={prefs.notify_desktop} onChange={(v) => updatePrefs({ notify_desktop: v })} />
              <div className="flex items-center justify-between py-2 text-[13.5px]">
                <span>Quick ask hotkey</span>
                <kbd className="rounded border border-line px-2 py-0.5 font-mono text-[11.5px]">{prefs.quick_ask_shortcut.replace('+', ' ')}</kbd>
              </div>
              {prefsError && <p role="alert" className="text-[12.5px] text-danger-ink">{prefsError}</p>}
              <Link to="/notifications" className="mt-1 inline-block text-[12.5px] text-accent-ink hover:underline">Notification settings</Link>
            </Card>
          )}

          <Card title="Account">
            <div className="flex items-center gap-3">
              <span className="grid h-9 w-9 flex-none place-items-center rounded-full border border-line bg-panel-2 text-[13px] font-bold text-accent-ink">
                {(user?.name?.[0] || 'U').toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[14px] font-medium">{user?.name}</div>
                <div className="truncate font-mono text-[11.5px] text-fg-dim">{user?.email}</div>
              </div>
            </div>
            <div className="mt-4 rounded-[10px] border border-danger/30 p-3">
              <button
                type="button"
                onClick={signOutEverywhere}
                disabled={signingOut}
                className="h-9 rounded-lg border border-danger/50 px-3 text-[13px] font-medium text-danger-ink hover:bg-danger/10 disabled:opacity-60"
              >
                {signingOut ? 'Signing out…' : 'Sign out of all devices'}
              </button>
              <p className="mt-2 text-[12px] text-fg-dim">Ends every session, here and on other devices. API keys keep working until you revoke them.</p>
              {signOutError && <p role="alert" className="mt-1 text-[12px] text-danger-ink">{signOutError}</p>}
            </div>
          </Card>

          <Card title="Providers and keys">
            <p className="text-[13px] leading-relaxed text-fg-dim">
              Provider keys come from the engine's environment (for example <span className="font-mono text-[12px]">OPENAI_API_KEY</span> in{' '}
              <span className="font-mono text-[12px]">{info ? `${info.data_folder}${info.data_folder.includes('\\') ? '\\' : '/'}.env` : '.env'}</span>). A custom base URL only receives a stored key when it's the provider's own address or listed in{' '}
              <span className="font-mono text-[12px]">COGNITRIX_TRUSTED_BASE_URLS</span>; other addresses are blocked.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link to="/api-keys" className="inline-flex h-9 items-center rounded-lg border border-line px-3 text-[13px] hover:bg-panel-2">
                Cognitrix API keys
              </Link>
            </div>
          </Card>

          <Card title="MCP servers" id="mcp-servers" className="lg:col-span-2">
            <McpServersCard />
          </Card>

          <Card title="Chat apps" id="chat-apps" className="lg:col-span-2">
            <ChatChannelsCard />
          </Card>

          <Card title="Voice input" id="voice-input">
            <VoiceSettingsCard />
          </Card>

          <Card title="Appearance">
            <div className="flex items-center justify-between text-[13.5px]">
              <span>Theme</span>
              <ThemeToggle />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
