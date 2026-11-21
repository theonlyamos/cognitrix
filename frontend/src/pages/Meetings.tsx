import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Link, useSearchParams } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { ComputerPanel } from '@/components/ComputerPanel';
import MarkdownMessage from '@/components/MarkdownMessage';
import { UpcomingCalls } from '@/components/UpcomingCalls';
import { PageHeader, LoadingState, ErrorState, EmptyState } from '@/components/list-ui';
import { cn } from '@/lib/utils';
import { tr, dateLocale } from '@/lib/i18n';

type Status = 'queued' | 'transcribing' | 'writing' | 'done' | 'error' | 'joining' | 'waiting' | 'live';
type ReplyMode = 'chat' | 'voice' | 'off';

export interface MeetingSummary {
  id: string;
  title: string;
  filename: string;
  agent_id: string;
  status: Status;
  progress: string;
  seconds: number;
  error: string;
  created_at: string;
  has_transcript: boolean;
  /** 'live': an agent sat in a call; 'recording': an upload. */
  kind?: 'recording' | 'live';
  url?: string;
  platform?: string;
  bot_name?: string;
  reply_mode?: ReplyMode;
  answers?: number;
  /** The agent is in the call right now. */
  in_call?: boolean;
}
interface Meeting extends MeetingSummary { transcript: string; notes: string }
interface LiveReadiness {
  /** The agents' sandboxed computer is on: calls run in its browser. */
  computer: boolean;
  /** A read-aloud provider has a key, so answers can be spoken. */
  speech: boolean;
}
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
interface AgentLite { id: string; name: string }

const ACCEPT = 'audio/*,video/*,.mp3,.m4a,.wav,.mp4,.webm,.ogg,.opus,.flac,.aac,.mov,.mkv';
const IN_CALL: Status[] = ['joining', 'waiting', 'live'];
const working = (status: Status) => status === 'queued' || status === 'transcribing' || status === 'writing'
  || IN_CALL.includes(status);
const PLATFORM: Record<string, string> = { meet: 'Google Meet', teams: 'Teams', zoom: 'Zoom' };

function length(seconds: number): string {
  if (!seconds) return '';
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return tr('ui.meetings.roundSec', { round: Math.round(seconds) });
  return minutes >= 60 ? tr('ui.meetings.floorHValueMin', { floor: Math.floor(minutes / 60), value: minutes % 60 }) : tr('ui.meetings.minutesMin', { minutes });
}

function day(stamp: string): string {
  const date = new Date(stamp.includes('T') ? stamp : `${stamp.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(dateLocale(), { month: 'short', day: 'numeric', year: 'numeric' });
}

function statusText(meeting: MeetingSummary): string {
  if (meeting.status === 'error') return 'failed';
  if (meeting.status === 'live') return ['in the call', length(meeting.seconds)].filter(Boolean).join(' · ');
  if (meeting.status === 'waiting') return 'waiting to be let in';
  if (meeting.status === 'joining') return meeting.progress || 'joining';
  if (meeting.status === 'done') return [day(meeting.created_at), length(meeting.seconds)].filter(Boolean).join(' · ');
  return meeting.progress || (meeting.status === 'queued' ? 'waiting to start' : meeting.status);
}

/** What an agent in a call shows: where it is, what it hears as it comes in,
 *  its screen on demand, and Leave. */
function LiveCall({ meeting, agents, onChanged, onReload }: {
  meeting: Meeting; agents: AgentLite[]; onChanged: () => void; onReload: () => Promise<unknown>;
}) {
  const [watching, setWatching] = useState(false);
  const [say, setSay] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const lines = meeting.transcript.split('\n').filter((l) => l.trim());

  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [lines.length]);

  const leave = async () => {
    setBusy(true);
    setError('');
    try {
      await api.post(`/meetings/${meeting.id}/leave`);
      await onReload();
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotReachTheAgent')));
    } finally {
      setBusy(false);
    }
  };
  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = say.trim();
    if (!text) return;
    setError('');
    try {
      await api.post(`/meetings/${meeting.id}/say`, { text });
      setSay('');
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotSendThatTo')));
    }
  };
  const where = PLATFORM[meeting.platform ?? ''] ?? 'the call';
  const state = meeting.status === 'live' ? `In ${where} as ${meeting.bot_name}`
    : meeting.status === 'waiting' ? `Waiting for someone in ${where} to let ${meeting.bot_name} in`
      : `Joining ${where}`;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex min-w-0 flex-1 items-center gap-2 text-[13px]" aria-live="polite">
          <span className="h-2 w-2 flex-none animate-pulse rounded-full bg-accent" aria-hidden />
          <span className="truncate">{state}{meeting.progress && meeting.status !== 'live' ? ` · ${meeting.progress}` : ''}</span>
        </p>
        <Button type="button" size="sm" variant="ghost" aria-pressed={watching} onClick={() => setWatching((v) => !v)}>
          {watching ? tr('ui.meetings.hideScreen') : tr('ui.meetings.watch')}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy || meeting.progress === 'leaving the call'} onClick={() => void leave()}>
          {meeting.progress === 'leaving the call' ? tr('ui.meetings.leaving') : tr('ui.meetings.leaveCall')}
        </Button>
      </div>
      {watching && (
        <div className="h-[420px] overflow-hidden rounded-[10px] border border-line">
          <ComputerPanel agents={agents} focusAgentId={meeting.agent_id} onClose={() => setWatching(false)} />
        </div>
      )}
      <div className="max-h-[360px] overflow-y-auto rounded-[10px] border border-line bg-bg px-3 py-2 text-[13px] leading-relaxed" aria-label={tr('ui.meetings.liveTranscript')}>
        {lines.length === 0 ? (
          <p className="text-fg-dim">{meeting.status === 'live' ? tr('ui.meetings.listeningWhatPeopleSayShows') : tr('ui.meetings.nothingHeardYet')}</p>
        ) : lines.map((line, i) => {
          const match = /^\[([\d:]+)\]\s*(.*)$/.exec(line);
          const mine = match && meeting.bot_name && match[2].startsWith(`${meeting.bot_name} (`);
          return (
            <p key={i} className={cn('flex gap-2 py-0.5', mine && 'text-accent-ink')}>
              {match && <span className="flex-none font-mono text-[11px] leading-[1.9] text-fg-dim">{match[1]}</span>}
              <span className="min-w-0 break-words">{match ? match[2] : line}</span>
            </p>
          );
        })}
        <div ref={end} />
      </div>
      {meeting.status === 'live' && (
        <form onSubmit={(e) => void send(e)} className="flex gap-2">
          <Input value={say} onChange={(e) => setSay(e.target.value)} maxLength={600}
            placeholder={meeting.reply_mode === 'voice' ? tr('ui.meetings.haveBotNameSaySomething', { bot_name: meeting.bot_name }) : tr('ui.meetings.haveBotNameWriteIn', { bot_name: meeting.bot_name })}
            aria-label={tr('ui.meetings.sayInTheCall')} className="h-9 flex-1" />
          <Button type="submit" size="sm" disabled={!say.trim()}>{tr('ui.meetings.send')}</Button>
        </form>
      )}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}

function MeetingDetail({ id, agents, onChanged }: { id: string; agents: AgentLite[]; onChanged: () => void }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [tab, setTab] = useState<'notes' | 'transcript'>('notes');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => api.get<Meeting>(`/meetings/${id}`).then((r) => setMeeting(r.data))
    .catch((e) => setError(errorMessage(e, tr('ui.meetings.couldNotLoadThisMeeting'))));

  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id]);

  const busyOnServer = meeting ? working(meeting.status) : false;
  useEffect(() => {
    if (!busyOnServer) return;
    const timer = window.setInterval(() => { void load().then(onChanged); }, 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busyOnServer, id]);

  if (!meeting) return <p className="px-4 py-3 text-[13px] text-fg-dim">{error || tr('ui.meetings.loading')}</p>;

  const text = tab === 'notes' ? meeting.notes : meeting.transcript;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: the text can be selected by hand */ }
  };
  const download = () => {
    const body = `# ${meeting.title}\n\n${meeting.notes}\n\n---\n\n## Transcript\n\n${meeting.transcript}\n`;
    const url = URL.createObjectURL(new Blob([body], { type: 'text/markdown' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${meeting.title.replace(/[^\w\- ]+/g, '').trim() || 'meeting'}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const act = async (work: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError('');
    try {
      await work();
      await load();
      onChanged();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
    }
  };
  const again = (agentId?: string) => act(
    () => api.post(`/meetings/${id}/notes`, agentId === undefined ? {} : { agent_id: agentId }),
    tr('ui.meetings.couldNotWriteTheNotes'),
  );
  const remove = async () => {
    if (!window.confirm(tr('ui.meetings.deleteTitleItsNotesAnd', { title: meeting.title }))) return;
    setBusy(true);
    try {
      await api.delete(`/meetings/${id}`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotDeleteTheMeeting')));
      setBusy(false);
    }
  };

  if (meeting.in_call || IN_CALL.includes(meeting.status)) {
    return (
      <div className="border-t border-line px-4 py-3">
        <LiveCall meeting={meeting} agents={agents} onChanged={onChanged} onReload={load} />
      </div>
    );
  }

  return (
    <div className="border-t border-line px-4 py-3">
      {busyOnServer ? (
        <p className="flex items-center gap-2 text-[13px] text-fg-dim" aria-live="polite">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
          {tr('ui.meetings.progressYouCanLeaveThis', { progress: meeting.progress || tr('ui.meetings.working') })}
        </p>
      ) : (
        <>
          {meeting.status === 'error' && <p role="alert" className="mb-2 text-[13px] text-danger-ink">{meeting.error}</p>}
          {(meeting.notes || meeting.transcript) && (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <div role="tablist" aria-label={tr('ui.meetings.meetingView')} className="flex rounded-lg border border-line p-0.5 text-[12.5px]">
                  {(['notes', 'transcript'] as const).map((name) => (
                    <button key={name} type="button" role="tab" aria-selected={tab === name} onClick={() => setTab(name)}
                      className={cn('min-h-9 rounded-md px-3 capitalize md:min-h-7', tab === name ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:text-fg')}>
                      {name}
                    </button>
                  ))}
                </div>
                <span className="flex-1" />
                <Button type="button" size="sm" variant="ghost" onClick={copy} disabled={!text}>{copied ? tr('ui.meetings.copied') : tr('ui.meetings.copy')}</Button>
                <Button type="button" size="sm" variant="ghost" onClick={download}>{tr('ui.meetings.download')}</Button>
              </div>
              {tab === 'notes' ? (
                meeting.notes ? <div className="md break-words text-[14px]"><MarkdownMessage content={meeting.notes} /></div>
                  : <p className="text-[13px] text-fg-dim">{tr('ui.meetings.noNotesYetTheTranscript')}</p>
              ) : (
                <p className="max-h-[420px] overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-fg-dim">{meeting.transcript}</p>
              )}
            </>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3 text-[12.5px]">
            {meeting.has_transcript && (
              <>
                <span className="text-fg-dim">{tr('ui.meetings.notesWrittenBy')}</span>
                <Select value={meeting.agent_id} disabled={busy} aria-label={tr('ui.meetings.agentThatWritesTheNotes')}
                  className="h-9 w-auto min-w-[150px]" onChange={(e) => void again(e.target.value)}>
                  {!agents.some((a) => a.id === meeting.agent_id) && <option value={meeting.agent_id}>{tr('ui.meetings.yourMainAgent')}</option>}
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
                <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void again()}>{tr('ui.meetings.writeAgain')}</Button>
              </>
            )}
            <span className="flex-1" />
            <button type="button" disabled={busy} onClick={() => void remove()} className="min-h-9 px-2 text-danger-ink hover:underline disabled:opacity-60">{tr('ui.meetings.delete')}</button>
          </div>
        </>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}

function JoinCall({ agents, live, onJoined, onCancel }: {
  agents: AgentLite[]; live?: LiveReadiness; onJoined: (id: string) => void; onCancel: () => void;
}) {
  const [url, setUrl] = useState('');
  const [agentId, setAgentId] = useState('');
  const [name, setName] = useState('');
  const [reply, setReply] = useState<ReplyMode>('chat');
  const [announce, setAnnounce] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await api.post<Meeting>('/meetings/live', {
        url: url.trim(), agent_id: agentId || null, name: name.trim() || null, reply_mode: reply, announce,
      });
      onJoined(res.data.id);
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotSendTheAgent')));
    } finally {
      setBusy(false);
    }
  };

  if (live && !live.computer) {
    return (
      <div className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[13px] leading-relaxed">
        {tr('ui.meetings.agentsJoinCallsFromThe')}{' '}
        <Link to="/settings/access" className="text-accent-ink hover:underline">{tr('ui.meetings.turnItOnUnderSettings')}</Link>{tr('ui.meetings.thenComeBack')}
        <button type="button" onClick={onCancel} className="ml-2 text-fg-dim hover:underline">{tr('ui.meetings.close')}</button>
      </div>
    );
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3 rounded-[10px] border border-line bg-panel px-4 py-3.5">
      <div>
        <p className="text-[14px] font-medium">{tr('ui.meetings.sendAnAgentIntoA_d72a')}</p>
        <p className="text-[12.5px] leading-relaxed text-fg-dim">
          {tr('ui.meetings.itJoinsFromTheBrowser')}
        </p>
      </div>
      <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
        {tr('ui.meetings.meetingLink')}
        <Input value={url} onChange={(e) => setUrl(e.target.value)} required type="url" inputMode="url"
          placeholder="https://meet.google.com/abc-defg-hij" className="h-9" autoFocus />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
          {tr('ui.meetings.agent')}
          <Select value={agentId} onChange={(e) => setAgentId(e.target.value)} className="h-9">
            <option value="">{tr('ui.meetings.yourMainAgent')}</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
          {tr('ui.meetings.nameInTheCall')}
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder={tr('ui.meetings.theAgentSName')} className="h-9" />
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
          {tr('ui.meetings.whenSomeoneSaysItsName')}
          <Select value={reply} onChange={(e) => setReply(e.target.value as ReplyMode)} className="h-9">
            <option value="chat">{tr('ui.meetings.answerInTheCallS')}</option>
            <option value="voice" disabled={live ? !live.speech : false}>{tr('ui.meetings.answerOutLoud')}{live && !live.speech ? tr('ui.meetings.needsAVoiceKey') : ''}</option>
            <option value="off">{tr('ui.meetings.donTAnswerOnlyTake')}</option>
          </Select>
        </label>
        <label className="flex items-center gap-2 self-end pb-2 text-[12.5px]">
          <input type="checkbox" checked={announce} onChange={(e) => setAnnounce(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
          {tr('ui.meetings.sayInTheChatThat')}
        </label>
      </div>
      <p className="text-[12px] leading-relaxed text-fg-dim">
        {tr('ui.meetings.letPeopleKnowAnAi')}
      </p>
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !url.trim()}>{busy ? tr('ui.meetings.sending') : tr('ui.meetings.joinTheCall')}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>{tr('ui.meetings.cancel')}</Button>
      </div>
    </form>
  );
}

export default function Meetings() {
  const { data, loading, error, refetch } = useResource<MeetingList>('/meetings');
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [agentId, setAgentId] = useState('');
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState<string | null>(params.get('open'));
  const [callId] = useState<string | null>(params.get('call'));
  const [joining, setJoining] = useState(false);
  const [uploading, setUploading] = useState('');
  const [uploadError, setUploadError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
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

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setUploadError('');
    if (data && file.size > data.max_bytes) {
      setUploadError(tr('ui.meetings.thatRecordingIsTooLarge'));
      return;
    }
    const form = new FormData();
    form.append('file', file, file.name);
    if (agentId) form.append('agent_id', agentId);
    setUploading(tr('ui.meetings.uploadingName', { name: file.name }));
    try {
      const res = await api.post<Meeting>('/meetings', form, {
        onUploadProgress: (p) => {
          if (p.total) setUploading(tr('ui.meetings.uploadingNameRound', { name: file.name, round: Math.round((p.loaded / p.total) * 100) }));
        },
      });
      setOpen(res.data.id);
      refetch({ silent: true });
    } catch (e) {
      setUploadError(errorMessage(e, tr('ui.meetings.couldNotUploadTheRecording')));
    } finally {
      setUploading('');
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.meetings.meetings')} subtitle={meetings.length > 0 ? tr('ui.meetings.sendAnAgentIntoA') : undefined}>
        <Button type="button" size="sm" variant="outline" disabled={joining || (data ? !data.voice_ready : true)} onClick={() => setJoining(true)}>
          {tr('ui.meetings.joinACall')}
        </Button>
        <input ref={fileInput} type="file" accept={ACCEPT} className="hidden" aria-label={tr('ui.meetings.meetingRecording')} onChange={upload} />
        <Button type="button" size="sm" disabled={!!uploading || (data ? !data.voice_ready : true)} onClick={() => fileInput.current?.click()}>
          {uploading ? tr('ui.meetings.uploading') : tr('ui.meetings.uploadRecording')}
        </Button>
      </PageHeader>
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <LoadingState label={tr('ui.meetings.loadingMeetings')} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-5 md:px-6">
            {data && !data.voice_ready && (
              <p className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[13px] leading-relaxed">
                {tr('ui.meetings.meetingNotesUseYourSpeech')}{' '}
                <Link to="/settings/voice" className="text-accent-ink hover:underline">{tr('ui.meetings.addAKeyUnderSettings')}</Link>{' '}{tr('ui.meetings.first')}
              </p>
            )}
            {data && data.voice_ready && !data.ffmpeg && (
              <p className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px] leading-relaxed text-fg-dim">
                {tr('ui.meetings.ffmpegIsnTInstalledWhere')}
              </p>
            )}
            {joining && (
              <JoinCall agents={agents} live={data?.live} onCancel={() => setJoining(false)}
                onJoined={(id) => { setJoining(false); setOpen(id); refetch({ silent: true }); }} />
            )}
            <UpcomingCalls agents={agents} highlight={callId}
              onOpenMeeting={(id) => { setOpen(id); refetch({ silent: true }); }} />
            {agents.length > 1 && (
              <label className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-dim">
                {tr('ui.meetings.notesWrittenBy')}
                <Select value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label={tr('ui.meetings.agentForNewRecordings')} className="h-9 w-auto min-w-[160px]">
                  <option value="">{tr('ui.meetings.yourMainAgent')}</option>
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </label>
            )}
            <div aria-live="polite" className="min-h-0 text-[12.5px]">
              {uploading && <p className="text-fg-dim">{uploading}</p>}
              {uploadError && <p role="alert" className="text-danger-ink">{uploadError}</p>}
            </div>
            {meetings.length === 0 ? (
              <EmptyState
                icon={<span aria-hidden className="font-mono text-lg">◉</span>}
                title={tr('ui.meetings.noMeetingsYet')}
                desc={tr('ui.meetings.sendAnAgentIntoA_c840')}
              />
            ) : (
              <ul className="divide-y divide-line rounded-[10px] border border-line bg-panel">
                {meetings.map((m) => (
                  <li key={m.id}>
                    <button type="button" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}
                      className="flex min-h-12 w-full flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-2.5 text-left hover:bg-panel-2">
                      <span className="min-w-0 flex-1 basis-[180px] truncate text-[14px] font-medium">
                        {m.title}
                        {m.kind === 'live' && <span className="ml-2 rounded bg-panel-2 px-1.5 py-0.5 align-middle font-mono text-[10.5px] font-normal text-fg-dim">{tr('ui.meetings.call')}</span>}
                      </span>
                      <span className={cn('flex items-center gap-1.5 font-mono text-[11px]', m.status === 'error' ? 'text-danger-ink' : 'text-fg-dim')}>
                        {working(m.status) && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />}
                        {statusText(m)}
                      </span>
                    </button>
                    {open === m.id && (
                      <MeetingDetail id={m.id} agents={agents} onChanged={() => refetch({ silent: true })} />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
