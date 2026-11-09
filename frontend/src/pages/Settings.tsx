import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useUser } from '@/context/AppContext';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { desktop, isDesktop, type DesktopSettings, type UpdateInfo } from '@/lib/desktop';
import { Toggle } from '@/pages/Notifications';
import { useT, type MessageKey } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { VoiceSettingsCard } from '@/components/VoiceSettingsCard';
import { McpServersCard } from '@/components/McpServersCard';
import { ConnectedAppsCard } from '@/components/ConnectedAppsCard';
import { ChatChannelsCard } from '@/components/ChatChannelsCard';
import { CredentialsCard } from '@/components/CredentialsCard';
import { ProviderKeys } from '@/components/settings/ProviderKeys';
import { AppearanceSettings } from '@/components/settings/Appearance';
import { MemorySettings } from '@/components/settings/Memory';
import { UsageSettings } from '@/components/settings/Usage';
import { PasswordChange } from '@/components/settings/PasswordChange';
import { SpokenRepliesSettings } from '@/components/settings/SpokenReplies';
import { PeopleSettings } from '@/components/settings/People';
import {
  Group, SectionHeader, SettingRow, SubHeading, buttonClass, quietButtonClass,
} from '@/components/settings/ui';

const onWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent);

const STATUS_TEXT = { starting: 'Starting', running: 'Running', stopped: 'Stopped', failed: 'Failed' } as const;

type SectionId = 'account' | 'appearance' | 'models' | 'usage' | 'memory' | 'voice' | 'apps' | 'access' | 'chat-apps' | 'people' | 'desktop' | 'engine';

interface SectionMeta {
  id: SectionId;
  label: string;
  group: string;
  /** Extra words the settings search matches. */
  keywords: string;
  desktopOnly?: boolean;
  /** Shown only to admins of a shared server. */
  adminOnly?: boolean;
}

const SECTIONS: SectionMeta[] = [
  { id: 'account', label: 'Account', group: 'GENERAL', keywords: 'profile email name password export backup download data sign out devices api keys' },
  { id: 'appearance', label: 'Appearance', group: 'GENERAL', keywords: 'theme dark light system send enter motion animation' },
  { id: 'models', label: 'Models and keys', group: 'AI', keywords: 'providers api key openai anthropic claude gemini google groq openrouter cerebras ollama' },
  { id: 'usage', label: 'Usage', group: 'AI', keywords: 'cost spend tokens money bill price how much' },
  { id: 'memory', label: 'Memory', group: 'AI', keywords: 'remember memories personalization forget history what agents know about me' },
  { id: 'voice', label: 'Voice', group: 'AI', keywords: 'input mic microphone speech transcription dictation spoken replies read aloud listen text to speech tts' },
  { id: 'apps', label: 'Connected apps', group: 'AGENTS', keywords: 'github gmail google calendar drive docs sheets slides notion linear sentry sign in oauth connectors integrations mcp' },
  { id: 'access', label: 'Agent access', group: 'AGENTS', keywords: 'working folder files mcp servers tools saved sign-ins credentials secrets passwords tokens' },
  { id: 'chat-apps', label: 'Chat apps', group: 'AGENTS', keywords: 'telegram slack discord bot alerts approvals' },
  { id: 'people', label: 'People', group: 'SERVER', keywords: 'users admin invite invitation members roles disable accounts email smtp reset', adminOnly: true },
  { id: 'desktop', label: 'Desktop app', group: 'THIS COMPUTER', keywords: 'startup windows login tray notifications hotkey quick ask update version upgrade', desktopOnly: true },
  { id: 'engine', label: 'Engine', group: 'THIS COMPUTER', keywords: 'server status restart log version database queue worker data folder' },
];

/** Old /settings#… links (from before sections had their own pages). */
const LEGACY_HASH: Record<string, SectionId> = {
  'voice-input': 'voice',
  'mcp-servers': 'access',
  'connected-apps': 'apps',
  'saved-sign-ins': 'access',
  'chat-apps': 'chat-apps',
};

function useDesktopPrefs(enabled: boolean) {
  const [prefs, setPrefs] = useState<DesktopSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    desktop.getSettings().then(setPrefs).catch((err: unknown) => {
      setError(`Couldn't read the desktop settings: ${typeof err === 'string' ? err : err instanceof Error ? err.message : 'unknown error'}`);
    });
  }, [enabled]);
  const update = async (patch: Parameters<typeof desktop.setSettings>[0]) => {
    setError(null);
    try {
      const next = await desktop.setSettings(patch);
      if (next) setPrefs(next);
    } catch (err) {
      setError(String(err));
    }
  };
  return { prefs, setPrefs, error, update };
}

function AccountSection() {
  const { user, logout } = useUser();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

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

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const exportData = async () => {
    setExporting(true);
    setExportError('');
    try {
      const res = await api.get('/account/export', { responseType: 'blob' });
      const url = URL.createObjectURL(res.data as Blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `cognitrix-export-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(errorMessage(err, 'Could not export your data.'));
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <SectionHeader title="Account" description="Who you're signed in as." />
      <Group>
        <SettingRow
          label={user?.name}
          detail={<span className="font-mono text-[11.5px]">{user?.email}</span>}
          lead={(
            <span className="grid h-9 w-9 flex-none place-items-center rounded-full border border-line bg-panel-2 text-[13px] font-bold text-accent-ink">
              {(user?.name?.[0] || 'U').toUpperCase()}
            </span>
          )}
        />
        <PasswordChange />
        <SettingRow label="Cognitrix API keys" detail="For scripts and other apps that call this engine">
          <Link to="/api-keys" className={buttonClass}>Manage</Link>
        </SettingRow>
        <SettingRow
          label="Export your data"
          detail="One file with your agents, chats, tasks, teams and memories. Keys, passwords and saved sign-ins are left out."
        >
          <button type="button" onClick={exportData} disabled={exporting} className={buttonClass}>
            {exporting ? 'Preparing…' : 'Download'}
          </button>
        </SettingRow>
        {exportError && <p role="alert" className="px-3.5 pb-3 text-[12px] text-danger-ink">{exportError}</p>}
      </Group>

      <SubHeading className="mt-7">DANGER ZONE</SubHeading>
      <Group danger>
        <SettingRow
          label="Sign out of all devices"
          detail="Ends every session, here and on other devices. API keys keep working until you revoke them."
        >
          <button
            type="button"
            onClick={signOutEverywhere}
            disabled={signingOut}
            className="inline-flex h-9 items-center rounded-lg border border-danger/50 px-3 text-[12.5px] font-medium text-danger-ink hover:bg-danger/10 disabled:opacity-60 md:h-8"
          >
            {signingOut ? 'Signing out…' : 'Sign out everywhere'}
          </button>
        </SettingRow>
        {signOutError && <p role="alert" className="px-3.5 pb-3 text-[12px] text-danger-ink">{signOutError}</p>}
      </Group>
    </>
  );
}

function AccessSection({ desktopApp }: { desktopApp: boolean }) {
  const { info, reload } = useSystemInfo();
  const { prefs, setPrefs } = useDesktopPrefs(desktopApp);

  const changeFolder = async () => {
    const folder = await desktop.pickWorkingFolder();
    if (folder) {
      setPrefs((p) => (p ? { ...p, working_folder: folder } : p));
      // The engine restarts to pick it up; ask again once it's back.
      setTimeout(reload, 4000);
    }
  };

  return (
    <>
      <SectionHeader title="Agent access" description="What agents can reach: files on this computer, extra tools, and services they sign in to." />

      <SubHeading>WORKING FOLDER</SubHeading>
      <Group>
        <SettingRow
          label={<span className="break-all font-mono text-[12.5px] font-normal">{info?.working_folder ?? prefs?.working_folder ?? '…'}</span>}
          detail={desktopApp
            ? "Shell commands and file tools only work inside this folder. Commands that point anywhere else are refused."
            : <>Shell commands and file tools only work inside this folder. Set it with <span className="font-mono">COGNITRIX_TOOLS_ROOT</span> on the server.</>}
        >
          {desktopApp && (
            <>
              <button type="button" onClick={() => desktop.openFolder('working')} className={quietButtonClass}>Open</button>
              <button type="button" onClick={changeFolder} className={buttonClass}>Change</button>
            </>
          )}
        </SettingRow>
      </Group>

      <SubHeading className="mt-7" >MCP SERVERS</SubHeading>
      <div id="mcp-servers" className="scroll-mt-4 rounded-[12px] border border-line bg-panel p-3.5">
        <McpServersCard />
      </div>

      <SubHeading className="mt-7">SAVED SIGN-INS</SubHeading>
      <div id="saved-sign-ins" className="scroll-mt-4 rounded-[12px] border border-line bg-panel p-3.5">
        <CredentialsCard />
      </div>
    </>
  );
}

const RELEASES_URL = 'https://github.com/theonlyamos/cognitrix/releases/latest';
const reason = (err: unknown, fallback: string) =>
  (typeof err === 'string' ? err : err instanceof Error ? err.message : fallback);

/** Settings › Desktop app: the installed version and the button that updates it. */
function UpdateRow() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [state, setState] = useState<'idle' | 'checking' | 'installing'>('checking');
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  const check = async (byHand: boolean) => {
    setState('checking');
    setError(null);
    try {
      setInfo(await desktop.checkForUpdate());
      setChecked(byHand);
    } catch (err) {
      setError(reason(err, "Couldn't check for updates."));
    } finally {
      setState('idle');
    }
  };

  useEffect(() => { void check(false); }, []);

  const install = async () => {
    setState('installing');
    setError(null);
    try {
      await desktop.installUpdate();
    } catch (err) {
      setError(reason(err, "The update couldn't be installed."));
      setState('idle');
    }
  };

  const detail = !info
    ? 'Checking…'
    : !info.enabled
      ? "This build doesn't update itself. Download new versions from the releases page."
      : info.available
        ? `Version ${info.available} is ready. Installing restarts the app and the engine.`
        : checked ? 'You have the newest version.' : 'Cognitrix checks for new versions twice a day.';

  return (
    <>
      <SettingRow label={`Version ${info?.current ?? '…'}`} detail={detail}>
        {info && !info.enabled && (
          <button type="button" onClick={() => void desktop.openExternal(RELEASES_URL)} className={buttonClass}>Open releases</button>
        )}
        {info?.enabled && !info.available && (
          <button type="button" onClick={() => void check(true)} disabled={state !== 'idle'} className={buttonClass}>
            {state === 'checking' ? 'Checking…' : 'Check for updates'}
          </button>
        )}
        {info?.enabled && info.available && (
          <button type="button" onClick={() => void install()} disabled={state !== 'idle'} className={buttonClass}>
            {state === 'installing' ? 'Installing…' : 'Install and restart'}
          </button>
        )}
      </SettingRow>
      {error && <p role="alert" className="px-3.5 py-2 text-[12.5px] text-danger-ink">{error}</p>}
    </>
  );
}

function DesktopSection() {
  const { prefs, error, update } = useDesktopPrefs(true);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const sendTest = async () => {
    setTestResult(null);
    try {
      await desktop.testNotification();
      setTestResult({ ok: true, text: 'Sent. If nothing appeared, check Focus / Do not disturb in your system settings.' });
    } catch (err) {
      setTestResult({ ok: false, text: typeof err === 'string' ? err : err instanceof Error ? err.message : 'The notification could not be shown.' });
    }
  };
  return (
    <>
      <SectionHeader title="Desktop app" description="How the app behaves on this computer." />
      {!prefs ? (
        !error && <p className="text-[13px] text-fg-dim">Loading…</p>
      ) : (
        <Group>
          <SettingRow label={onWindows ? 'Start with Windows' : 'Start at login'} detail="Adds Cognitrix to your startup apps. It opens in the tray and starts the engine, so agents and automations are ready">
            <Toggle label={onWindows ? 'Start with Windows' : 'Start at login'} checked={prefs.start_with_windows} onChange={(v) => update({ start_with_windows: v })} />
          </SettingRow>
          <SettingRow label="Keep running in the tray when closed" detail="Tasks keep running while the window is closed">
            <Toggle label="Keep running in the tray when closed" checked={prefs.close_to_tray} onChange={(v) => update({ close_to_tray: v })} />
          </SettingRow>
          <SettingRow
            label={onWindows ? 'Windows notifications' : 'System notifications'}
            detail={<>Choose which events under <Link to="/notifications" className="text-accent-ink hover:underline">Notifications</Link></>}
          >
            <button type="button" onClick={() => void sendTest()} className={quietButtonClass}>Send test</button>
            <Toggle label={onWindows ? 'Windows notifications' : 'System notifications'} checked={prefs.notify_desktop} onChange={(v) => update({ notify_desktop: v })} />
          </SettingRow>
          {testResult && (
            <p role={testResult.ok ? 'status' : 'alert'} className={cn('px-3.5 py-2 text-[12.5px]', testResult.ok ? 'text-fg-dim' : 'text-danger-ink')}>
              {testResult.text}
            </p>
          )}
          <SettingRow label="Quick ask hotkey" detail="Opens a small ask box from anywhere">
            <kbd className="rounded border border-line px-2 py-0.5 font-mono text-[11.5px]">{prefs.quick_ask_shortcut.replace('+', ' ')}</kbd>
          </SettingRow>
          <UpdateRow />
        </Group>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
    </>
  );
}

function EngineSection({ desktopApp }: { desktopApp: boolean }) {
  const engine = useOptionalNotifications()?.engine ?? null;
  const { info } = useSystemInfo();
  const engineState = engine?.status ?? (info ? 'running' : 'starting');
  const engineAddress = engine?.url?.replace(/^https?:\/\//, '') ?? window.location.host;
  const dot = engineState === 'running' ? 'bg-ok' : engineState === 'starting' ? 'bg-[#ffb86b]' : 'bg-danger';
  const value = (v: ReactNode) => <span className="break-all text-right font-mono text-[12px]">{v}</span>;

  return (
    <>
      <SectionHeader
        title={desktopApp ? 'Engine' : 'Server'}
        description={desktopApp ? 'The engine runs your agents and their tasks on this computer.' : 'The Cognitrix server your agents run on.'}
      />
      <Group>
        <SettingRow
          label={STATUS_TEXT[engineState]}
          detail={<span className="font-mono text-[11.5px]">{engineAddress} · v{info?.version ?? '…'}</span>}
          lead={<span aria-hidden="true" className={cn('ml-1 h-2 w-2 flex-none rounded-full', dot)} />}
        >
          {desktopApp && (
            <>
              <button type="button" onClick={() => desktop.openFolder('logs')} className={quietButtonClass}>Open log</button>
              <button type="button" onClick={() => desktop.restartEngine()} className={buttonClass}>Restart</button>
            </>
          )}
        </SettingRow>
        {engine?.error && <p role="alert" className="px-3.5 py-2 text-[12.5px] text-danger-ink">{engine.error}</p>}
        <SettingRow label="Database">{value(info ? (info.database === 'sqlite' ? 'SQLite' : info.database) : '…')}</SettingRow>
        <SettingRow label="Task queue">
          {value(info ? `${info.task_queue === 'filesystem' ? 'local queue' : 'Redis'}${info.worker_running == null ? '' : info.worker_running ? ' · worker running' : ' · worker idle'}` : '…')}
        </SettingRow>
        <SettingRow label="Data folder">
          {desktopApp ? (
            <button type="button" onClick={() => desktop.openFolder('data')} className="hover:underline">{value(info?.data_folder ?? '…')}</button>
          ) : value(info?.data_folder ?? '…')}
        </SettingRow>
      </Group>
    </>
  );
}

function AppearanceSection() {
  const t = useT();
  return (
    <>
      <SectionHeader title={t('settings.section.appearance')} description={t('appearance.description')} />
      <AppearanceSettings />
    </>
  );
}

function SectionBody({ id, desktopApp }: { id: SectionId; desktopApp: boolean }) {
  switch (id) {
    case 'account': return <AccountSection />;
    case 'appearance':
      return <AppearanceSection />;
    case 'models':
      return (<><SectionHeader title="Models and keys" description="API keys for the model providers your agents use." /><ProviderKeys /></>);
    case 'usage':
      return (<><SectionHeader title="Usage" description="How many tokens your agents used and roughly what that cost." /><UsageSettings /></>);
    case 'memory':
      return (
        <>
          <SectionHeader
            title="Memory"
            description="What your agents remember about you between chats. Only you can see these, and agents never see another person's."
          />
          <MemorySettings />
        </>
      );
    case 'voice':
      return (
        <>
          <SectionHeader title="Voice" description="Talk to your agents and have their replies read to you." />
          <SubHeading>VOICE INPUT</SubHeading>
          <div className="rounded-[12px] border border-line bg-panel p-3.5"><VoiceSettingsCard /></div>
          <SubHeading className="mt-7">SPOKEN REPLIES</SubHeading>
          <SpokenRepliesSettings />
        </>
      );
    case 'apps':
      return (
        <>
          <SectionHeader
            title="Connected apps"
            description="Sign in to apps so your agents can use them. Each connection is yours: other people on this server can't see or use it. Pick which apps an agent may use on its Connections tab."
          />
          <ConnectedAppsCard />
        </>
      );
    case 'access': return <AccessSection desktopApp={desktopApp} />;
    case 'chat-apps':
      return (
        <>
          <SectionHeader
            title="Chat apps"
            description="Chat with your agents from Telegram, Slack or Discord, and get alerts and task approvals there. You create the bot, so messages go only between your bot and this engine."
          />
          <ChatChannelsCard />
        </>
      );
    case 'people':
      return (
        <>
          <SectionHeader title="People" description="Who has an account on this server, what they used, and invitations for new people." />
          <PeopleSettings />
        </>
      );
    case 'desktop': return <DesktopSection />;
    case 'engine': return <EngineSection desktopApp={desktopApp} />;
  }
}

function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useT();
  return (
    <label className="flex h-10 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 text-fg-dim focus-within:border-accent md:h-9">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t('settings.search')}
        aria-label={t('settings.search')}
        className="min-w-0 flex-1 bg-transparent text-[13px] text-fg outline-none placeholder:text-fg-dim"
      />
    </label>
  );
}

function matches(section: SectionMeta, query: string) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = `${section.label} ${section.keywords}`.toLowerCase();
  return q.split(/\s+/).every((word) => hay.includes(word));
}

export default function Settings() {
  const desktopApp = isDesktop();
  const { section } = useParams<{ section?: string }>();
  const location = useLocation();
  const [query, setQuery] = useState('');
  const t = useT();
  const { user } = useUser();
  const admin = Boolean(user?.is_admin) && !desktopApp;

  const visible = useMemo(
    () => SECTIONS.filter((s) => (desktopApp || !s.desktopOnly) && (admin || !s.adminOnly)),
    [desktopApp, admin],
  );
  const shown = visible.filter(
    (s) => matches(s, query) || t(`settings.section.${s.id}` as MessageKey).toLowerCase().includes(query.trim().toLowerCase()),
  );
  const active = visible.find((s) => s.id === section);

  // Scroll a #anchor inside the section into view once it renders.
  useEffect(() => {
    const id = location.hash.slice(1);
    if (!id) return;
    const t = setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: 'start' }), 150);
    return () => clearTimeout(t);
  }, [location.hash, section]);

  if (!section) {
    const legacy = LEGACY_HASH[location.hash.slice(1)];
    if (legacy) {
      const anchor = legacy === 'access' ? location.hash : '';
      return <Navigate to={`/settings/${legacy}${anchor}`} replace />;
    }
  } else if (!active) {
    return <Navigate to="/settings" replace />;
  }

  // Wide screens with no section picked show the first one.
  const current = active ?? visible[0];
  const groups = Array.from(new Set(shown.map((s) => s.group)));
  const GROUP_KEY: Record<string, MessageKey> = {
    GENERAL: 'settings.group.general', AI: 'settings.group.ai', AGENTS: 'settings.group.agents',
    SERVER: 'settings.group.server', 'THIS COMPUTER': 'settings.group.computer',
  };
  const groupLabel = (g: string) => t(g === 'THIS COMPUTER' && !desktopApp ? 'settings.group.system' : GROUP_KEY[g]);
  const labelFor = (s: SectionMeta) =>
    t(s.id === 'engine' && !desktopApp ? 'settings.section.server' : (`settings.section.${s.id}` as MessageKey));

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 bg-bg text-fg">
      {/* Section menu: a column on wide screens, the whole page on phones until a section is picked. */}
      <nav
        aria-label="Settings sections"
        className={cn(
          'min-h-0 flex-none overflow-y-auto border-line md:flex md:w-[232px] md:flex-col md:border-r md:px-2.5 md:py-5',
          active ? 'hidden' : 'flex w-full flex-col px-4 py-5',
        )}
      >
        <h1 className="mb-3 ml-10 px-1 text-[22px] font-semibold md:sr-only">{t('settings.title')}</h1>
        <div className="mb-2 md:mx-1">
          <SearchBox value={query} onChange={setQuery} />
        </div>
        {groups.map((g) => (
          <div key={g} className="md:contents">
            <div className="px-2.5 pb-1.5 pt-4 font-mono text-[10px] tracking-[0.14em] text-fg-dim md:pt-3.5">{groupLabel(g)}</div>
            <div className="overflow-hidden rounded-[12px] border border-line bg-panel md:contents">
              {shown.filter((s) => s.group === g).map((s) => (
                <Link
                  key={s.id}
                  to={`/settings/${s.id}`}
                  aria-current={current.id === s.id ? 'page' : undefined}
                  className={cn(
                    'flex items-center gap-2 border-t border-line px-3.5 py-3 text-[14px] first:border-t-0 md:rounded-lg md:border-t-0 md:px-2.5 md:py-[7px] md:text-[13px]',
                    current.id === s.id
                      ? 'md:bg-panel-2 md:font-medium md:text-fg md:shadow-[inset_2px_0_0_rgb(var(--accent-rgb))]'
                      : 'md:text-fg-dim md:hover:bg-panel-2 md:hover:text-fg',
                  )}
                >
                  <span className="flex-1">{labelFor(s)}</span>
                  <span aria-hidden="true" className="text-fg-dim md:hidden">›</span>
                </Link>
              ))}
            </div>
          </div>
        ))}
        {shown.length === 0 && <p className="px-2.5 pt-4 text-[13px] text-fg-dim">{t('settings.noMatch', { query })}</p>}
      </nav>

      <main
        className={cn('min-h-0 min-w-0 flex-1 overflow-y-auto', active ? 'block' : 'hidden md:block')}
        aria-label={labelFor(current)}
      >
        <div className="w-full max-w-[780px] px-9 py-6 max-md:px-4 max-md:py-4">
          {active && (
            <Link to="/settings" className="mb-3 ml-11 inline-flex h-9 items-center gap-1.5 text-[13px] text-fg-dim hover:text-fg md:hidden">
              <span aria-hidden="true">←</span> {t('settings.title')}
            </Link>
          )}
          <SectionBody key={current.id} id={current.id} desktopApp={desktopApp} />
        </div>
      </main>
    </div>
  );
}
