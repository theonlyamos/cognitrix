import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AudioLines, ChevronDown, FlaskConical, Settings2, Upload, Video } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { UpNext, useCalendar } from '@/components/UpcomingCalls';
import { MeetingSearch, MeetingSettingsSheet } from '@/components/MeetingExtras';
import { MeetingDetail } from '@/components/meetings/MeetingDetail';
import { JoinCallDialog } from '@/components/meetings/JoinCallDialog';
import { Menu, MenuItem } from '@/components/meetings/Overlay';
import { Badge } from '@/components/meetings/PaneHeader';
import {
  IN_CALL, PLATFORM, clock, day, dayGroup, length, working,
  type AgentLite, type LiveReadiness, type Meeting, type MeetingSummary,
} from '@/components/meetings/types';
import { PageHeader, LoadingState, ErrorState } from '@/components/list-ui';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

export type { MeetingSummary } from '@/components/meetings/types';

interface MeetingList {
  meetings: MeetingSummary[];
  /** A speech-to-text provider with a key is set under Settings, Voice input. */
  voice_ready: boolean;
  /** ffmpeg is installed where the engine runs, so video and long files work. */
  ffmpeg: boolean;
  max_bytes: number;
  max_seconds: number;
  live?: LiveReadiness;
}

type Filter = 'all' | 'calls' | 'recordings';
type Group = 'live' | 'today' | 'yesterday' | 'earlier';

const ACCEPT = 'audio/*,video/*,.mp3,.m4a,.wav,.mp4,.webm,.ogg,.opus,.flac,.aac,.mov,.mkv';
const UPLOAD_AGENT = 'cognitrix.meetings.uploadAgent';
const GROUPS: Group[] = ['live', 'today', 'yesterday', 'earlier'];

const readUploadAgent = () => { try { return localStorage.getItem(UPLOAD_AGENT) ?? ''; } catch { return ''; } };

/** The second line of a list row: where it happened, who was there, how long. */
function rowMeta(m: MeetingSummary): string {
  if (m.kind !== 'live') return ['Recording', length(m.seconds)].filter(Boolean).join(' · ');
  const where = m.test ? 'Test call' : PLATFORM[m.platform ?? ''] ?? 'Call';
  return [where, m.bot_name, length(m.seconds)].filter(Boolean).join(' · ');
}

/** The right end of a list row: a badge when it needs a look, else the time. */
function RowEnd({ m, group }: { m: MeetingSummary; group: Group }) {
  if (m.status === 'error') return <Badge tone="fail">{m.kind === 'live' ? tr('ui.meetings.couldnTJoin') : tr('ui.meetings.failed')}</Badge>;
  if (m.status === 'live' && m.owner_question) return <Badge tone="warn" pulse={false}>{tr('ui.meetings.question')}</Badge>;
  if (m.status === 'live' && m.paused) return <Badge tone="plain">{tr('ui.meetingExtras.offTheRecord')}</Badge>;
  if (m.status === 'waiting') return <Badge tone="plain" pulse>{tr('ui.meetings.waiting')}</Badge>;
  if (m.status === 'joining') return <Badge tone="plain" pulse>{tr('ui.meetings.joining')}</Badge>;
  if (m.status === 'live') return null;
  if (working(m.status)) return <Badge tone="plain" pulse>{m.status === 'queued' ? tr('ui.meetings.queued') : m.status === 'transcribing' ? tr('ui.meetings.transcribing') : tr('ui.meetings.writing')}</Badge>;
  return <span className="flex-none font-mono text-[11px] text-fg-dim">{group === 'earlier' ? day(m.created_at).replace(/,? \d{4}$/, '') : clock(m.created_at)}</span>;
}

export default function Meetings() {
  const { data, loading, error, refetch } = useResource<MeetingList>('/meetings');
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState<string | null>(params.get('open'));
  const [callId] = useState<string | null>(params.get('call'));
  const [filter, setFilter] = useState<Filter>('all');
  const [joining, setJoining] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [uploadAgent, setUploadAgent] = useState(readUploadAgent);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const calendar = useCalendar();
  const meetings = data?.meetings ?? [];

  // An Inbox alert links here with ?open=<id> (a meeting) or ?call=<id> (a
  // calendar call); keep the address clean after.
  useEffect(() => {
    if (params.get('open') || params.get('call')) {
      const next = new URLSearchParams(params);
      next.delete('open');
      next.delete('call');
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    api.get<AgentLite[]>('/agents').then((r) => setAgents((r.data ?? []).filter((a) => a.id))).catch(() => {});
  }, []);

  // Rows that are still being worked on refresh themselves.
  const anyWorking = meetings.some((m) => working(m.status));
  useEffect(() => {
    if (!anyWorking) return;
    const timer = window.setInterval(() => refetch({ silent: true }), 4000);
    return () => window.clearInterval(timer);
  }, [anyWorking, refetch]);

  const show = (id: string) => { setOpen(id); refetch({ silent: true }); };
  const changeUploadAgent = (id: string) => {
    setUploadAgent(id);
    try { localStorage.setItem(UPLOAD_AGENT, id); } catch { /* private window: kept for this visit only */ }
  };

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (data && file.size > data.max_bytes) {
      setNotice({ text: tr('ui.meetings.thatRecordingIsTooLarge'), error: true });
      return;
    }
    const form = new FormData();
    form.append('file', file, file.name);
    if (uploadAgent && agents.some((a) => a.id === uploadAgent)) form.append('agent_id', uploadAgent);
    setNotice({ text: tr('ui.meetings.uploadingName', { name: file.name }) });
    setUploading(true);
    try {
      const res = await api.post<Meeting>('/meetings', form, {
        onUploadProgress: (p) => {
          if (p.total) setNotice({ text: tr('ui.meetings.uploadingNameRound', { name: file.name, round: Math.round((p.loaded / p.total) * 100) }) });
        },
      });
      setNotice(null);
      show(res.data.id);
    } catch (e) {
      setNotice({ text: errorMessage(e, tr('ui.meetings.couldNotUploadTheRecording')), error: true });
    } finally {
      setUploading(false);
    }
  };

  const testCall = async (agentId = '') => {
    setTesting(true);
    setNotice(null);
    try {
      const res = await api.post<Meeting>('/meetings/test', { agent_id: agentId || null });
      setJoining(false);
      show(res.data.id);
    } catch (e) {
      setNotice({ text: errorMessage(e, tr('ui.meetings.couldNotStartTheTest')), error: true });
      setJoining(false);
    } finally {
      setTesting(false);
    }
  };

  const voiceReady = !!data?.voice_ready;
  const computer = !!data?.live?.computer;
  const shown = meetings.filter((m) => filter === 'all' || (filter === 'calls' ? m.kind === 'live' : m.kind !== 'live'));
  const groupOf = (m: MeetingSummary): Group => (IN_CALL.includes(m.status) ? 'live' : dayGroup(m.created_at));
  const groupLabel: Record<Group, string> = { live: 'Live now', today: 'Today', yesterday: 'Yesterday', earlier: 'Earlier' };
  const counts = { calls: meetings.filter((m) => m.kind === 'live').length, recordings: meetings.filter((m) => m.kind !== 'live').length };

  const newMenu = (
    <Menu label={tr('ui.meetings.new')} trigger={({ open: menuOpen, toggle }) => (
      <div className="flex">
        <Button type="button" size="sm" className="rounded-r-none pl-3 pr-2.5" disabled={!voiceReady} onClick={() => setJoining(true)}>
          <Video size={15} aria-hidden /><span className="hidden sm:inline">{tr('ui.meetings.sendAgentToACall')}</span><span className="sm:hidden">{tr('ui.meetings.join')}</span>
        </Button>
        <Button type="button" size="sm" aria-label={tr('ui.meetings.moreWaysToAddA')} aria-expanded={menuOpen} disabled={!voiceReady}
          className="rounded-l-none border-l border-black/15 px-2" onClick={toggle}>
          <ChevronDown size={15} aria-hidden />
        </Button>
      </div>
    )}>
      {(close) => (
        <>
          <MenuItem icon={<Video size={15} />} title={tr('ui.meetings.sendAgentToACall')} hint={tr('ui.meetings.meetTeamsOrZoomLink')} onSelect={() => { close(); setJoining(true); }} />
          <MenuItem icon={<Upload size={15} />} title={tr('ui.meetings.uploadRecording')} hint={tr('ui.meetings.audioOrVideoNotesAfter')} disabled={uploading}
            onSelect={() => { close(); fileInput.current?.click(); }} />
          <MenuItem icon={<FlaskConical size={15} />} title={tr('ui.meetings.testCall')} hint={tr('ui.meetings.checkJoiningHearingAndThe')}
            disabled={testing || !computer || !voiceReady} onSelect={() => { close(); void testCall(); }} />
        </>
      )}
    </Menu>
  );

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.meetings.meetings')}>
        <MeetingSearch className="hidden w-[300px] md:block lg:w-[340px]" onOpen={show} />
        <button type="button" onClick={() => setSettingsOpen(true)} aria-label={tr('ui.meetingExtras.meetingSettings')} title={tr('ui.meetingExtras.meetingSettings')}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8">
          <Settings2 size={17} aria-hidden />
        </button>
        {newMenu}
        <input ref={fileInput} type="file" accept={ACCEPT} className="hidden" aria-label={tr('ui.meetings.meetingRecording')} onChange={(e) => void upload(e)} />
      </PageHeader>
      {loading && !data ? (
        <LoadingState label={tr('ui.meetings.loadingMeetings')} />
      ) : error ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className={cn('min-h-0 w-full flex-col overflow-y-auto md:flex md:w-[340px] md:flex-none md:border-r md:border-line lg:w-[360px]', open ? 'hidden' : 'flex')}>
            <div className="flex flex-col gap-3 px-3 pb-2 pt-3">
              <MeetingSearch className="md:hidden" onOpen={show} />
              {data && !data.voice_ready && (
                <p className="rounded-[10px] border border-line bg-panel px-3 py-2 text-[12.5px] leading-relaxed">
                  {tr('ui.meetings.meetingNotesUseYourSpeech')}{' '}
                  <Link to="/settings/voice" className="text-accent-ink hover:underline">{tr('ui.meetings.addAKeyUnderSettings')}</Link>{' '}{tr('ui.meetings.first')}
                </p>
              )}
              {data && data.voice_ready && !data.ffmpeg && (
                <p className="rounded-[10px] border border-line bg-panel px-3 py-2 text-[12px] leading-relaxed text-fg-dim">{tr('ui.meetings.ffmpegIsnTInstalledWhere')}</p>
              )}
              {notice && (
                <p role={notice.error ? 'alert' : 'status'} className={cn('text-[12.5px]', notice.error ? 'text-danger-ink' : 'text-fg-dim')}>{notice.text}</p>
              )}
              <UpNext calendar={calendar} agents={agents} highlight={callId} onOpenMeeting={show} onOpenSettings={() => setSettingsOpen(true)} />
              {meetings.length > 0 && (
                <div role="tablist" aria-label={tr('ui.meetings.show')} className="flex self-start rounded-lg border border-line bg-panel p-0.5 text-[12.5px]">
                  {(['all', 'calls', 'recordings'] as const).map((f) => (
                    <button key={f} type="button" role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}
                      className={cn('flex min-h-9 items-center gap-1.5 rounded-md px-3 md:min-h-7', filter === f ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:text-fg')}>
                      {f === 'all' ? tr('ui.meetings.all') : f === 'calls' ? tr('ui.meetings.calls') : tr('ui.meetings.recordings')}
                      {f !== 'all' && counts[f] > 0 && <span className="font-mono text-[10.5px] opacity-70">{counts[f]}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {meetings.length === 0 ? (
              <div className="flex flex-col items-start gap-2 px-4 py-6">
                <p className="text-[14px] font-medium">{tr('ui.meetings.noMeetingsYet')}</p>
                <p className="text-[12.5px] leading-relaxed text-fg-dim">{tr('ui.meetings.sendAnAgentIntoA_c840')}</p>
              </div>
            ) : shown.length === 0 ? (
              <p className="px-4 py-4 text-[12.5px] text-fg-dim">{tr('ui.meetings.nothingHereYet')}</p>
            ) : (
              <nav aria-label={tr('ui.meetings.meetings')} className="pb-4">
                {GROUPS.map((group) => {
                  const rows = shown.filter((m) => groupOf(m) === group);
                  if (rows.length === 0) return null;
                  return (
                    <div key={group}>
                      <h3 className="px-4 pb-1 pt-3 text-[10.5px] font-medium uppercase tracking-[.08em] text-fg-dim">{groupLabel[group]}</h3>
                      <ul className="flex flex-col gap-0.5 px-2">
                        {rows.map((m) => {
                          const Icon = m.kind === 'live' ? (m.test ? FlaskConical : Video) : AudioLines;
                          const hot = group === 'live';
                          return (
                            <li key={m.id}>
                              <button type="button" aria-current={open === m.id ? 'true' : undefined} onClick={() => setOpen(m.id)}
                                className={cn('flex min-h-14 w-full items-center gap-3 rounded-[9px] px-2.5 py-2 text-left', open === m.id ? 'bg-panel-2' : 'hover:bg-panel-2/60')}>
                                <span className={cn('flex h-8 w-8 flex-none items-center justify-center rounded-lg', hot ? 'bg-accent text-accent-foreground' : 'bg-panel-2 text-fg-dim')}>
                                  <Icon size={15} aria-hidden />
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-[13.5px] font-medium">{m.title}</span>
                                  <span className="block truncate text-[12px] text-fg-dim">{rowMeta(m)}</span>
                                </span>
                                <RowEnd m={m} group={group} />
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  );
                })}
              </nav>
            )}
          </div>
          <main className={cn('min-h-0 min-w-0 flex-1 flex-col', open ? 'flex' : 'hidden md:flex')} aria-label={tr('ui.meetings.meeting')}>
            {open ? (
              <MeetingDetail key={open} id={open} agents={agents} onBack={() => setOpen(null)} onOpen={show}
                onChanged={() => refetch({ silent: true })} />
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-panel-2 text-fg-dim"><AudioLines size={20} aria-hidden /></span>
                <p className="text-[14px] font-medium">{meetings.length ? tr('ui.meetings.pickAMeetingToSee') : tr('ui.meetings.noMeetingsYet')}</p>
                <p className="max-w-[420px] text-[12.5px] leading-relaxed text-fg-dim">
                  {tr('ui.meetings.sendAnAgentIntoA_9dd3')}
                </p>
                <div className="flex gap-2">
                  <Button type="button" size="sm" variant="outline" disabled={!voiceReady} onClick={() => setJoining(true)}><Video size={14} aria-hidden />{' '}{tr('ui.meetings.sendAgentToACall')}</Button>
                  <Button type="button" size="sm" variant="ghost" disabled={!voiceReady || uploading} onClick={() => fileInput.current?.click()}><Upload size={14} aria-hidden /> {tr('ui.meetings.uploadRecording')}</Button>
                </div>
              </div>
            )}
          </main>
        </div>
      )}
      <JoinCallDialog open={joining} agents={agents} live={data?.live} testing={testing}
        onTest={(agentId) => void testCall(agentId)} onClose={() => setJoining(false)}
        onJoined={(id) => { setJoining(false); show(id); }} />
      <MeetingSettingsSheet open={settingsOpen} onClose={() => setSettingsOpen(false)} calendar={calendar} agents={agents}
        uploadAgent={uploadAgent} onUploadAgent={changeUploadAgent} />
    </div>
  );
}
