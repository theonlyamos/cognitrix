import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Link } from 'react-router-dom';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Select } from '@/lib/components/ui/select';
import MarkdownMessage from '@/components/MarkdownMessage';
import { PageHeader, LoadingState, ErrorState, EmptyState } from '@/components/list-ui';
import { cn } from '@/lib/utils';

type Status = 'queued' | 'transcribing' | 'writing' | 'done' | 'error';

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
}
interface Meeting extends MeetingSummary { transcript: string; notes: string }
interface MeetingList {
  meetings: MeetingSummary[];
  /** A speech-to-text provider with a key is set under Settings, Voice input. */
  voice_ready: boolean;
  /** ffmpeg is installed where the engine runs, so video and long files work. */
  ffmpeg: boolean;
  max_bytes: number;
  max_seconds: number;
}
interface AgentLite { id: string; name: string }

const ACCEPT = 'audio/*,video/*,.mp3,.m4a,.wav,.mp4,.webm,.ogg,.opus,.flac,.aac,.mov,.mkv';
const working = (status: Status) => status === 'queued' || status === 'transcribing' || status === 'writing';

function length(seconds: number): string {
  if (!seconds) return '';
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return `${Math.round(seconds)} sec`;
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
}

function day(stamp: string): string {
  const date = new Date(stamp.includes('T') ? stamp : `${stamp.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function statusText(meeting: MeetingSummary): string {
  if (meeting.status === 'error') return 'failed';
  if (meeting.status === 'done') return [day(meeting.created_at), length(meeting.seconds)].filter(Boolean).join(' · ');
  return meeting.progress || (meeting.status === 'queued' ? 'waiting to start' : meeting.status);
}

function MeetingDetail({ id, agents, onChanged }: { id: string; agents: AgentLite[]; onChanged: () => void }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [tab, setTab] = useState<'notes' | 'transcript'>('notes');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => api.get<Meeting>(`/meetings/${id}`).then((r) => setMeeting(r.data))
    .catch((e) => setError(errorMessage(e, 'Could not load this meeting.')));

  useEffect(() => { void load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id]);

  const busyOnServer = meeting ? working(meeting.status) : false;
  useEffect(() => {
    if (!busyOnServer) return;
    const timer = window.setInterval(() => { void load().then(onChanged); }, 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busyOnServer, id]);

  if (!meeting) return <p className="px-4 py-3 text-[13px] text-fg-dim">{error || 'Loading…'}</p>;

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
    'Could not write the notes again.',
  );
  const remove = async () => {
    if (!window.confirm(`Delete “${meeting.title}”, its notes and its transcript?`)) return;
    setBusy(true);
    try {
      await api.delete(`/meetings/${id}`);
      onChanged();
    } catch (e) {
      setError(errorMessage(e, 'Could not delete the meeting.'));
      setBusy(false);
    }
  };

  return (
    <div className="border-t border-line px-4 py-3">
      {busyOnServer ? (
        <p className="flex items-center gap-2 text-[13px] text-fg-dim" aria-live="polite">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
          {meeting.progress || 'Working'}… You can leave this page; it keeps going.
        </p>
      ) : (
        <>
          {meeting.status === 'error' && <p role="alert" className="mb-2 text-[13px] text-danger-ink">{meeting.error}</p>}
          {(meeting.notes || meeting.transcript) && (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <div role="tablist" aria-label="Meeting view" className="flex rounded-lg border border-line p-0.5 text-[12.5px]">
                  {(['notes', 'transcript'] as const).map((name) => (
                    <button key={name} type="button" role="tab" aria-selected={tab === name} onClick={() => setTab(name)}
                      className={cn('min-h-9 rounded-md px-3 capitalize md:min-h-7', tab === name ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:text-fg')}>
                      {name}
                    </button>
                  ))}
                </div>
                <span className="flex-1" />
                <Button type="button" size="sm" variant="ghost" onClick={copy} disabled={!text}>{copied ? 'Copied' : 'Copy'}</Button>
                <Button type="button" size="sm" variant="ghost" onClick={download}>Download</Button>
              </div>
              {tab === 'notes' ? (
                meeting.notes ? <div className="md break-words text-[14px]"><MarkdownMessage content={meeting.notes} /></div>
                  : <p className="text-[13px] text-fg-dim">No notes yet. The transcript is saved, so they can be written again.</p>
              ) : (
                <p className="max-h-[420px] overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-fg-dim">{meeting.transcript}</p>
              )}
            </>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3 text-[12.5px]">
            {meeting.has_transcript && (
              <>
                <span className="text-fg-dim">Notes written by</span>
                <Select value={meeting.agent_id} disabled={busy} aria-label="Agent that writes the notes"
                  className="h-9 w-auto min-w-[150px]" onChange={(e) => void again(e.target.value)}>
                  {!agents.some((a) => a.id === meeting.agent_id) && <option value={meeting.agent_id}>Your main agent</option>}
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
                <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void again()}>Write again</Button>
              </>
            )}
            <span className="flex-1" />
            <button type="button" disabled={busy} onClick={() => void remove()} className="min-h-9 px-2 text-danger-ink hover:underline disabled:opacity-60">Delete</button>
          </div>
        </>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}

export default function Meetings() {
  const { data, loading, error, refetch } = useResource<MeetingList>('/meetings');
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [agentId, setAgentId] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [uploading, setUploading] = useState('');
  const [uploadError, setUploadError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const meetings = data?.meetings ?? [];

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
      setUploadError('That recording is too large (1 GB at most).');
      return;
    }
    const form = new FormData();
    form.append('file', file, file.name);
    if (agentId) form.append('agent_id', agentId);
    setUploading(`Uploading ${file.name}…`);
    try {
      const res = await api.post<Meeting>('/meetings', form, {
        onUploadProgress: (p) => {
          if (p.total) setUploading(`Uploading ${file.name}… ${Math.round((p.loaded / p.total) * 100)}%`);
        },
      });
      setOpen(res.data.id);
      refetch({ silent: true });
    } catch (e) {
      setUploadError(errorMessage(e, 'Could not upload the recording.'));
    } finally {
      setUploading('');
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title="Meetings" subtitle="upload a recording, get the notes, decisions and action items · private to you">
        <input ref={fileInput} type="file" accept={ACCEPT} className="hidden" aria-label="Meeting recording" onChange={upload} />
        <Button type="button" size="sm" disabled={!!uploading || (data ? !data.voice_ready : true)} onClick={() => fileInput.current?.click()}>
          {uploading ? 'Uploading…' : 'Upload recording'}
        </Button>
      </PageHeader>
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <LoadingState label="loading meetings…" />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-5 md:px-6">
            {data && !data.voice_ready && (
              <p className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[13px] leading-relaxed">
                Meeting notes use your speech-to-text provider to transcribe the recording.{' '}
                <Link to="/settings/voice" className="text-accent-ink hover:underline">Add a key under Settings › Voice</Link> first.
              </p>
            )}
            {data && data.voice_ready && !data.ffmpeg && (
              <p className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-[12.5px] leading-relaxed text-fg-dim">
                ffmpeg isn't installed where Cognitrix runs, so only WAV files and audio files under 25 MB work.
                Install ffmpeg to use video and long recordings in any format.
              </p>
            )}
            {agents.length > 1 && (
              <label className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-dim">
                Notes written by
                <Select value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label="Agent for new recordings" className="h-9 w-auto min-w-[160px]">
                  <option value="">Your main agent</option>
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
                title="No meetings yet"
                desc="Upload the audio or video of a meeting (up to 4 hours). It is transcribed with your voice provider, then one of your agents writes a summary, the decisions and who agreed to do what. The recording is deleted once it has been transcribed."
              />
            ) : (
              <ul className="divide-y divide-line rounded-[10px] border border-line bg-panel">
                {meetings.map((m) => (
                  <li key={m.id}>
                    <button type="button" aria-expanded={open === m.id} onClick={() => setOpen(open === m.id ? null : m.id)}
                      className="flex min-h-12 w-full flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-2.5 text-left hover:bg-panel-2">
                      <span className="min-w-0 flex-1 basis-[180px] truncate text-[14px] font-medium">{m.title}</span>
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
