import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, FlaskConical } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Textarea } from '@/lib/components/ui/textarea';
import { Choice } from '@/components/MeetingExtras';
import { Modal } from '@/components/meetings/Overlay';
import { PLATFORM, platformOf, type AgentLite, type LiveReadiness, type Meeting, type ReplyMode, type ShareLevel } from '@/components/meetings/types';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

/** Send an agent into a call: the link, who goes and how it answers; the
 *  background, alerts and answer level are folded away. */
export function JoinCallDialog({ open, agents, live, testing, onTest, onJoined, onClose }: {
  open: boolean;
  agents: AgentLite[];
  live?: LiveReadiness;
  testing: boolean;
  onTest: (agentId: string) => void;
  onJoined: (id: string) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState('');
  const [agentId, setAgentId] = useState('');
  const [name, setName] = useState('');
  const [reply, setReply] = useState<ReplyMode>('chat');
  const [announce, setAnnounce] = useState(true);
  const [more, setMore] = useState(false);
  const [context, setContext] = useState('');
  const [watch, setWatch] = useState('');
  const [share, setShare] = useState<ShareLevel | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setUrl(''); setName(''); setContext(''); setWatch(''); setShare(''); setMore(false); setError(''); setReply('chat'); setAnnounce(true);
  }, [open]);

  const platform = platformOf(url);
  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.post<Meeting>('/meetings/live', {
        url: url.trim(), agent_id: agentId || null, name: name.trim() || null, reply_mode: reply, announce,
        context: context.trim() || null, watch_words: watch.trim() || null, share: share || null,
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
      <Modal open={open} title={tr('ui.meetings.joinCallDialog.sendAnAgentToA')} onClose={onClose}>
        <p className="text-[13px] leading-relaxed">
          {tr('ui.meetings.agentsJoinCallsFromThe')}{' '}
          <Link to="/settings/access" className="text-accent-ink hover:underline">{tr('ui.meetings.turnItOnUnderSettings')}</Link>{tr('ui.meetings.thenComeBack')}
        </p>
      </Modal>
    );
  }

  const agentName = agents.find((a) => a.id === agentId)?.name;
  return (
    <Modal open={open} title={tr('ui.meetings.joinCallDialog.sendAnAgentToA')} onClose={onClose} footer={(
      <>
        <p className="min-w-0 flex-1 basis-[220px] text-[11.5px] leading-snug text-fg-dim">{tr('ui.meetings.joinCallDialog.letPeopleKnowAnAi')}</p>
        <Button type="button" size="sm" variant="ghost" disabled={testing} title={tr('ui.meetings.checkJoiningTheCallChat')} onClick={() => onTest(agentId)}>
          <FlaskConical size={14} aria-hidden /> {testing ? tr('ui.meetings.starting') : tr('ui.meetings.joinCallDialog.testFirst')}
        </Button>
        <Button type="button" size="sm" disabled={busy || !url.trim()} onClick={() => void submit()}>{busy ? tr('ui.meetings.sending') : tr('ui.meetings.joinTheCall')}</Button>
      </>
    )}>
      <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3.5 pt-1">
        <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
          {tr('ui.meetings.meetingLink')}
          <span className="relative">
            <Input value={url} onChange={(e) => setUrl(e.target.value)} required type="url" inputMode="url" autoFocus
              placeholder="https://meet.google.com/abc-defg-hij" className={cn('h-10', platform && 'pr-28')} />
            {platform && (
              <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-accent-ink/10 px-2 py-0.5 text-[11.5px] font-medium text-accent-ink">
                {PLATFORM[platform]}
              </span>
            )}
          </span>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            {tr('ui.meetings.agent')}
            <Select value={agentId} onChange={(e) => setAgentId(e.target.value)} className="h-9">
              <option value="">{tr('ui.meetings.yourMainAgent')}</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
            {tr('ui.meetings.nameInTheCall')}
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder={agentName || tr('ui.meetings.theAgentSName')} className="h-9" />
          </label>
        </div>
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-[12px] text-fg-dim">{tr('ui.meetings.whenSomeoneSaysItsName')}</legend>
          <Choice name="reply" value={reply} onChange={setReply} disabled={live && !live.speech ? ['voice'] : []} options={[
            { id: 'chat', label: tr('ui.meetings.joinCallDialog.answerInChat') },
            { id: 'voice', label: live && !live.speech ? `${tr('ui.meetings.answerOutLoud')}${tr('ui.meetings.needsAVoiceKey')}` : tr('ui.meetings.answerOutLoud') },
            { id: 'off', label: tr('ui.meetings.joinCallDialog.justTakeNotes') },
          ]} />
        </fieldset>
        <label className="flex items-center gap-2 text-[12.5px]">
          <input type="checkbox" checked={announce} onChange={(e) => setAnnounce(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
          {tr('ui.meetings.sayInTheChatThat')}
        </label>
        <div className="rounded-[10px] border border-line">
          <button type="button" aria-expanded={more} onClick={() => setMore((v) => !v)}
            className="flex min-h-10 w-full items-center gap-2 px-3 text-left text-[12.5px]">
            <ChevronRight size={14} aria-hidden className={cn('flex-none text-fg-dim transition-transform', more && 'rotate-90')} />
            <span className="font-medium">{tr('ui.meetings.joinCallDialog.backgroundAlertsAndAnswers')}</span>
            <span className="text-fg-dim">{tr('ui.meetings.joinCallDialog.optional')}</span>
          </button>
          {more && (
            <div className="flex flex-col gap-3 border-t border-line px-3 py-3">
              <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
                {tr('ui.meetings.backgroundForTheAgentOptional')}
                <Textarea value={context} onChange={(e) => setContext(e.target.value)} rows={3} maxLength={6000}
                  placeholder={tr('ui.meetings.theAgendaWhoIsIn')} />
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
                  {tr('ui.meetings.alertMeIfTheseCome')}
                  <Input value={watch} onChange={(e) => setWatch(e.target.value)} maxLength={2000} placeholder={tr('ui.meetings.budgetMyName')} className="h-9" />
                </label>
                <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
                  {tr('ui.meetings.answersMayUse')}
                  <Select value={share} onChange={(e) => setShare(e.target.value as ShareLevel | '')} className="h-9">
                    <option value="">{tr('ui.meetings.yourMeetingSetting')}</option>
                    <option value="call">{tr('ui.meetings.onlyTheCallAndIts')}</option>
                    <option value="work">{tr('ui.meetings.alsoYourMemoryKnowledgeAnd')}</option>
                  </Select>
                </label>
              </div>
              <p className="text-[11.5px] text-fg-dim">{tr('ui.meetings.joinCallDialog.callsYouSendFromUp')}</p>
            </div>
          )}
        </div>
        {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
