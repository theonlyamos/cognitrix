import { useEffect, useState } from 'react';
import { Check, ChevronLeft, Copy, Download, MoreHorizontal, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Select } from '@/lib/components/ui/select';
import MarkdownMessage from '@/components/MarkdownMessage';
import { MeetingTabs, SeenOnJoin } from '@/components/MeetingExtras';
import { LiveCall } from '@/components/meetings/LiveCall';
import { Menu, MenuItem } from '@/components/meetings/Overlay';
import { Badge, PaneHeader } from '@/components/meetings/PaneHeader';
import { IN_CALL, PLATFORM, clock, day, dayGroup, length, working, type AgentLite, type Meeting } from '@/components/meetings/types';
import { tr } from '@/lib/i18n';

type Tab = 'notes' | 'transcript' | 'speakers' | 'background';

function IconButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button type="button" title={label} aria-label={label} onClick={onClick} disabled={disabled}
      className="flex h-10 w-10 items-center justify-center rounded-lg border border-line text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-50 md:h-8 md:w-8">
      {children}
    </button>
  );
}

/** When and how long, where, and who: the line under a meeting's title. */
function metaLine(meeting: Meeting): string {
  const group = dayGroup(meeting.created_at);
  const at = group === 'today' ? `Today, ${clock(meeting.created_at)}` : group === 'yesterday' ? `Yesterday, ${clock(meeting.created_at)}` : day(meeting.created_at);
  const where = meeting.kind === 'live' ? (meeting.test ? 'Test call' : PLATFORM[meeting.platform ?? ''] ?? 'Call') : 'Recording';
  return [at, length(meeting.seconds), where, meeting.kind === 'live' && meeting.bot_name ? `as ${meeting.bot_name}` : ''].filter(Boolean).join(' · ');
}

/** The right side of the page: one meeting, live or finished. */
export function MeetingDetail({ id, agents, onBack, onChanged, onOpen }: {
  id: string; agents: AgentLite[]; onBack: () => void; onChanged: () => void; onOpen: (id: string) => void;
}) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [tab, setTab] = useState<Tab>('notes');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => api.get<Meeting>(`/meetings/${id}`).then((r) => { setMeeting(r.data); setError(''); })
    .catch((e) => setError(errorMessage(e, tr('ui.meetings.couldNotLoadThisMeeting'))));

  useEffect(() => {
    setMeeting(null);
    setTab('notes');
    void load();
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [id]);

  const busyOnServer = meeting ? working(meeting.status) : false;
  useEffect(() => {
    if (!busyOnServer) return;
    const timer = window.setInterval(() => { void load().then(onChanged); }, 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busyOnServer, id]);

  if (!meeting) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <button type="button" onClick={onBack}
          className="mx-2 mt-2 inline-flex h-10 items-center gap-1 self-start rounded-lg px-2 text-[13px] text-fg-dim hover:bg-panel-2 hover:text-fg md:hidden">
          <ChevronLeft size={18} aria-hidden />{tr('ui.meetings.paneHeader.backToMeetings')}
        </button>
        <p className="px-6 py-5 text-[13px] text-fg-dim">{error || tr('ui.meetings.loading')}</p>
      </div>
    );
  }

  if (IN_CALL.includes(meeting.status)) {
    return <LiveCall meeting={meeting} agents={agents} onBack={onBack} onChanged={onChanged} onReload={load} />;
  }

  const tabs: Tab[] = ['notes', 'transcript'];
  if (meeting.speaker_transcript) tabs.push('speakers');
  if (meeting.context) tabs.push('background');
  const text = tab === 'notes' ? meeting.notes : tab === 'speakers' ? meeting.speaker_transcript || ''
    : tab === 'background' ? meeting.context || '' : meeting.transcript;
  const actions = meeting.action_items ?? [];

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
  const retry = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await api.post<Meeting>('/meetings/live', {
        url: meeting.url, title: meeting.title, agent_id: meeting.agent_id || null, name: meeting.bot_name || null, reply_mode: meeting.reply_mode || 'chat',
        announce: meeting.announce ?? true, context: meeting.context || null, watch_words: (meeting.watch_words ?? []).join(', ') || null, share: meeting.share || null,
      });
      onChanged();
      onOpen(res.data.id);
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotSendTheAgent')));
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!window.confirm(tr('ui.meetings.deleteTitleItsNotesAnd', { title: meeting.title }))) return;
    setBusy(true);
    try {
      await api.delete(`/meetings/${id}`);
      onBack();
      onChanged();
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetings.couldNotDeleteTheMeeting')));
      setBusy(false);
    }
  };

  const failed = meeting.status === 'error';
  const badge = failed ? <Badge tone="fail">{meeting.kind === 'live' ? tr('ui.meetings.meetingDetail.couldnTJoin') : tr('ui.meetings.meetingDetail.failed')}</Badge>
    : busyOnServer ? <Badge tone="plain" pulse>{meeting.progress || tr('ui.meetings.working')}</Badge>
      : meeting.test ? <Badge tone="plain">{tr('ui.meetings.test')}</Badge> : null;
  const more = (
    <Menu label={tr('ui.meetings.meetingDetail.more')} trigger={({ open, toggle }) => (
      <button type="button" aria-label={tr('ui.meetings.meetingDetail.more')} aria-expanded={open} onClick={toggle}
        className="flex h-10 w-10 items-center justify-center rounded-lg border border-line text-fg-dim hover:bg-panel-2 hover:text-fg md:h-8 md:w-8">
        <MoreHorizontal size={15} aria-hidden />
      </button>
    )}>
      {(close) => <MenuItem danger icon={<Trash2 size={14} />} title={tr('ui.meetings.delete')} disabled={busy} onSelect={() => { close(); void remove(); }} />}
    </Menu>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneHeader title={meeting.title} badge={badge} meta={metaLine(meeting)} onBack={onBack} actions={(
        <>
          {meeting.has_transcript && !busyOnServer && (
            <>
              <Select value={meeting.agent_id} disabled={busy} aria-label={tr('ui.meetings.agentThatWritesTheNotes')} title={tr('ui.meetings.notesWrittenBy')}
                className="h-10 w-auto min-w-[140px] text-[12.5px] md:h-8" onChange={(e) => void again(e.target.value)}>
                {!agents.some((a) => a.id === meeting.agent_id) && <option value={meeting.agent_id}>{tr('ui.meetings.yourMainAgent')}</option>}
                {agents.map((a) => <option key={a.id} value={a.id}>{tr('ui.meetings.meetingDetail.notesByName', { name: a.name })}</option>)}
              </Select>
              <IconButton label={tr('ui.meetings.writeAgain')} disabled={busy} onClick={() => void again()}><RefreshCw size={14} aria-hidden /></IconButton>
            </>
          )}
          {(meeting.notes || meeting.transcript) && (
            <>
              <IconButton label={copied ? tr('ui.meetings.copied') : tr('ui.meetings.copy')} disabled={!text} onClick={() => void copy()}>
                {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
              </IconButton>
              <IconButton label={tr('ui.meetings.download')} onClick={download}><Download size={14} aria-hidden /></IconButton>
            </>
          )}
          {more}
        </>
      )} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {busyOnServer ? (
          <p className="flex items-center gap-2 px-3 py-4 text-[13px] text-fg-dim md:px-6" aria-live="polite">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
            {tr('ui.meetings.progressYouCanLeaveThis', { progress: meeting.progress || tr('ui.meetings.working') })}
          </p>
        ) : (
          <div className="flex flex-col gap-4 px-3 py-4 md:px-6">
            {failed && (
              <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-danger/30 bg-danger/5 px-3 py-2.5">
                <p role="alert" className="min-w-0 flex-1 text-[13px] text-danger-ink">{meeting.error}</p>
                {meeting.kind === 'live' && meeting.url && !meeting.test && (
                  <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void retry()}>
                    <RotateCcw size={14} aria-hidden /> {tr('ui.upcomingCalls.tryAgain')}
                  </Button>
                )}
              </div>
            )}
            {failed && meeting.kind === 'live' && (
              <SeenOnJoin meetingId={meeting.id} seen={meeting.seen || ''} hasImage={!!meeting.has_seen_image} />
            )}
            {(meeting.notes || meeting.transcript) && (
              <div className="flex flex-col gap-4">
                <MeetingTabs tabs={tabs} value={tab} onChange={setTab} labels={{
                  notes: 'Notes', transcript: 'Transcript', speakers: 'By speaker', background: tr('ui.meetings.backgroundTheAgentHad'),
                }} />
                <div className="flex flex-col gap-6 wide:flex-row">
                  <div className="min-w-0 flex-1 wide:max-w-[720px]">
                    {tab === 'notes' ? (
                      meeting.notes ? <div className="md break-words text-[14px]"><MarkdownMessage content={meeting.notes} /></div>
                        : <p className="text-[13px] text-fg-dim">{tr('ui.meetings.noNotesYetTheTranscript')}</p>
                    ) : (
                      <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-fg-dim">{text}</p>
                    )}
                  </div>
                  {tab === 'notes' && actions.length > 0 && (
                    <aside className="flex-none rounded-[10px] border border-line px-4 py-3 wide:w-[280px]" aria-label={tr('ui.meetings.meetingDetail.actionItems')}>
                      <h3 className="mb-1 text-[11px] font-medium uppercase tracking-[.08em] text-fg-dim">{tr('ui.meetings.meetingDetail.actionItemsLength', { length: actions.length })}</h3>
                      <ul className="divide-y divide-line">
                        {actions.map((item, i) => (
                          <li key={i} className="py-2 text-[13px] leading-snug">
                            {item.owner && <span className="font-medium">{item.owner} </span>}
                            <span className="text-fg-dim">{item.what}</span>
                          </li>
                        ))}
                      </ul>
                    </aside>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
        {error && <p role="alert" className="px-3 pb-4 text-[12.5px] text-danger-ink md:px-6">{error}</p>}
      </div>
    </div>
  );
}
