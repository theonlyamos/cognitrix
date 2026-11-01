import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useUser } from '@/context/AppContext';
import { useOptionalNotifications } from '@/context/NotificationsContext';
import { desktop, isDesktop, type DesktopSettings } from '@/lib/desktop';
import { Toggle } from '@/pages/Notifications';
import { cn } from '@/lib/utils';
import { useSystemInfo } from '@/hooks/useSystemInfo';
import { VoiceSettingsCard } from '@/components/VoiceSettingsCard';
import { McpServersCard } from '@/components/McpServersCard';
import { ChatChannelsCard } from '@/components/ChatChannelsCard';
import { CredentialsCard } from '@/components/CredentialsCard';
import { ProviderKeys } from '@/components/settings/ProviderKeys';
import { AppearanceSettings } from '@/components/settings/Appearance';
import {
  Group, SectionHeader, SettingRow, SubHeading, buttonClass, quietButtonClass,
} from '@/components/settings/ui';

const onWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent);

const STATUS_TEXT = { starting: 'Starting', running: 'Running', stopped: 'Stopped', failed: 'Failed' } as const;

type SectionId = 'account' | 'appearance' | 'models' | 'voice' | 'access' | 'chat-apps' | 'desktop' | 'engine';

interface SectionMeta {
  id: SectionId;
  label: string;
  group: string;
  /** Extra words the settings search matches. */
  keywords: string;
  desktopOnly?: boolean;
}

const SECTIONS: SectionMeta[] = [
  { id: 'account', label: 'Account', group: 'GENERAL', keywords: 'profile email name sign out devices api keys' },
  { id: 'appearance', label: 'Appearance', group: 'GENERAL', keywords: 'theme dark light system send enter motion animation' },
  { id: 'models', label: 'Models and keys', group: 'AI', keywords: 'providers api key openai anthropic claude gemini google groq openrouter cerebras ollama' },
  { id: 'voice', label: 'Voice input', group: 'AI', keywords: 'mic microphone speech transcription dictation' },
  { id: 'access', label: 'Agent access', group: 'AGENTS', keywords: 'working folder files mcp servers tools saved sign-ins credentials secrets passwords tokens' },
  { id: 'chat-apps', label: 'Chat apps', group: 'AGENTS', keywords: 'telegram slack bot alerts approvals' },
  { id: 'desktop', label: 'Desktop app', group: 'THIS COMPUTER', keywords: 'startup windows login tray notifications hotkey quick ask', desktopOnly: true },
  { id: 'engine', label: 'Engine', group: 'THIS COMPUTER', keywords: 'server status restart log version database queue worker data folder' },
];

/** Old /settings#… links (from before sections had their own pages). */
const LEGACY_HASH: Record<string, SectionId> = {
  'voice-input': 'voice',
  'mcp-servers': 'access',
  'saved-sign-ins': 'access',
  'chat-apps': 'chat-apps',
};

function useDesktopPrefs(enabled: boolean) {
  const [prefs, setPrefs] = useState<DesktopSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (enabled) void desktop.getSettings().then(setPrefs);
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
        <SettingRow label="Cognitrix API keys" detail="For scripts and other apps that call this engine">
          <Link to="/api-keys" className={buttonClass}>Manage</Link>
        </SettingRow>
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

function DesktopSection() {
  const { prefs, error, update } = useDesktopPrefs(true);
  return (
    <>
      <SectionHeader title="Desktop app" description="How the app behaves on this computer." />
      {!prefs ? (
        <p className="text-[13px] text-fg-dim">Loading…</p>
      ) : (
        <Group>
          <SettingRow label={onWindows ? 'Start with Windows' : 'Start at login'} detail="Opens in the tray when you sign in">
            <Toggle label={onWindows ? 'Start with Windows' : 'Start at login'} checked={prefs.start_with_windows} onChange={(v) => update({ start_with_windows: v })} />
          </SettingRow>
          <SettingRow label="Keep running in the tray when closed" detail="Tasks keep running while the window is closed">
            <Toggle label="Keep running in the tray when closed" checked={prefs.close_to_tray} onChange={(v) => update({ close_to_tray: v })} />
          </SettingRow>
          <SettingRow
            label={onWindows ? 'Windows notifications' : 'System notifications'}
            detail={<>Choose which events under <Link to="/notifications" className="text-accent-ink hover:underline">Notifications</Link></>}
          >
            <Toggle label={onWindows ? 'Windows notifications' : 'System notifications'} checked={prefs.notify_desktop} onChange={(v) => update({ notify_desktop: v })} />
          </SettingRow>
          <SettingRow label="Quick ask hotkey" detail="Opens a small ask box from anywhere">
            <kbd className="rounded border border-line px-2 py-0.5 font-mono text-[11.5px]">{prefs.quick_ask_shortcut.replace('+', ' ')}</kbd>
          </SettingRow>
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

function SectionBody({ id, desktopApp }: { id: SectionId; desktopApp: boolean }) {
  switch (id) {
    case 'account': return <AccountSection />;
    case 'appearance':
      return (<><SectionHeader title="Appearance" description="How Cognitrix looks and behaves on this device." /><AppearanceSettings /></>);
    case 'models':
      return (<><SectionHeader title="Models and keys" description="API keys for the model providers your agents use." /><ProviderKeys /></>);
    case 'voice':
      return (
        <>
          <SectionHeader title="Voice input" />
          <div className="rounded-[12px] border border-line bg-panel p-3.5"><VoiceSettingsCard /></div>
        </>
      );
    case 'access': return <AccessSection desktopApp={desktopApp} />;
    case 'chat-apps':
      return (
        <>
          <SectionHeader
            title="Chat apps"
            description="Chat with your agents from Telegram or Slack, and get alerts and task approvals there. You create the bot, so messages go only between your bot and this engine."
          />
          <ChatChannelsCard />
        </>
      );
    case 'desktop': return <DesktopSection />;
    case 'engine': return <EngineSection desktopApp={desktopApp} />;
  }
}

function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex h-10 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 text-fg-dim focus-within:border-accent md:h-9">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search settings"
        aria-label="Search settings"
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

  const visible = useMemo(() => SECTIONS.filter((s) => desktopApp || !s.desktopOnly), [desktopApp]);
  const shown = visible.filter((s) => matches(s, query));
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
  const groupLabel = (g: string) => (g === 'THIS COMPUTER' && !desktopApp ? 'SYSTEM' : g);
  const labelFor = (s: SectionMeta) => (s.id === 'engine' && !desktopApp ? 'Server' : s.label);

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
        <h1 className="mb-3 ml-10 px-1 text-[22px] font-semibold md:sr-only">Settings</h1>
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
        {shown.length === 0 && <p className="px-2.5 pt-4 text-[13px] text-fg-dim">No settings match “{query}”.</p>}
      </nav>

      <main
        className={cn('min-h-0 min-w-0 flex-1 overflow-y-auto', active ? 'block' : 'hidden md:block')}
        aria-label={labelFor(current)}
      >
        <div className="w-full max-w-[780px] px-9 py-6 max-md:px-4 max-md:py-4">
          {active && (
            <Link to="/settings" className="mb-3 ml-11 inline-flex h-9 items-center gap-1.5 text-[13px] text-fg-dim hover:text-fg md:hidden">
              <span aria-hidden="true">←</span> Settings
            </Link>
          )}
          <SectionBody key={current.id} id={current.id} desktopApp={desktopApp} />
        </div>
      </main>
    </div>
  );
}
