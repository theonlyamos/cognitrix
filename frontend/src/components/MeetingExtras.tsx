import { useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Textarea } from '@/lib/components/ui/textarea';
import MarkdownMessage from '@/components/MarkdownMessage';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

/** What answers in a call may use: only the call, or also the person's work. */
export type ShareLevel = 'call' | 'work';

export interface MeetingSettings {
  share: ShareLevel;
  watch_words: string;
  vocabulary: string;
  speaker_pass: boolean;
  live_notes: boolean;
}

interface SearchHit { id: string; title: string; date: string; kind: string; snippet: string }

/** The person's private controls while an agent is in a call: an answer to
 *  its question, guidance, something to raise, and off the record. Nobody in
 *  the call sees any of it. */
export function CallControls({ meetingId, botName, paused, question, guidance, liveNotes }: {
  meetingId: string; botName: string; paused: boolean; question: string; guidance: string[]; liveNotes: string;
}) {
  const [kind, setKind] = useState<'note' | 'raise'>('note');
  const [text, setText] = useState('');
  const [answer, setAnswer] = useState('');
  const [sent, setSent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const post = async (path: string, body: object, done: string) => {
    setBusy(true);
    setError('');
    try {
      await api.post(`/meetings/${meetingId}/${path}`, body);
      setSent(done);
      window.setTimeout(() => setSent(''), 4000);
      return true;
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetingExtras.couldNotReachTheAgent')));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const steer = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    const done = kind === 'raise' ? `${botName} brings it up at the next pause.` : `${botName} uses it in its answers.`;
    if (await post('steer', { kind, text: text.trim() }, done)) setText('');
  };
  const reply = async (event: FormEvent) => {
    event.preventDefault();
    if (!answer.trim()) return;
    if (await post('steer', { kind: 'answer', text: answer.trim() }, tr('ui.meetingExtras.botnamePassesYourAnswerOn', { botName }))) setAnswer('');
  };

  return (
    <div className="flex flex-col gap-3">
      {question && (
        <form onSubmit={(e) => void reply(e)} role="alert"
          className="flex flex-col gap-2 rounded-[10px] border border-accent bg-panel px-3 py-2.5">
          <p className="text-[13px] leading-relaxed">
            <span className="font-medium">{tr('ui.meetingExtras.botnameIsAskingYou', { botName })}</span> {question}
          </p>
          <div className="flex gap-2">
            <Input value={answer} onChange={(e) => setAnswer(e.target.value)} maxLength={600} autoFocus
              placeholder={tr('ui.meetingExtras.yourAnswerPassedOnIn')} aria-label={tr('ui.meetingExtras.yourAnswer')} className="h-9 flex-1" />
            <Button type="submit" size="sm" disabled={busy || !answer.trim()}>{tr('ui.meetingExtras.answer')}</Button>
          </div>
        </form>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {paused && (
          <span className="rounded bg-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-fg-dim">{tr('ui.meetingExtras.offTheRecord')}</span>
        )}
        <Button type="button" size="sm" variant="outline" disabled={busy}
          onClick={() => void post('pause', { paused: !paused }, paused ? tr('ui.meetingExtras.backOnTheRecord') : tr('ui.meetingExtras.pausedNothingIsKeptUntil'))}>
          {paused ? tr('ui.meetingExtras.resumeNotes') : tr('ui.meetingExtras.pauseNotes')}
        </Button>
        <span className="text-[12px] text-fg-dim">
          {tr('ui.meetingExtras.anyoneInTheCallCan', { botName })}
        </span>
      </div>
      <form onSubmit={(e) => void steer(e)} className="flex flex-col gap-2 rounded-[10px] border border-line px-3 py-2.5">
        <p className="text-[12.5px] text-fg-dim">{tr('ui.meetingExtras.tellBotnamePrivatelyNobodyIn', { botName })}</p>
        <div className="flex flex-wrap gap-2">
          <Select value={kind} onChange={(e) => setKind(e.target.value as 'note' | 'raise')} aria-label={tr('ui.meetingExtras.whatToDoWithIt')}
            className="h-9 w-auto">
            <option value="note">{tr('ui.meetingExtras.useInAnswers')}</option>
            <option value="raise">{tr('ui.meetingExtras.bringItUp')}</option>
          </Select>
          <Input value={text} onChange={(e) => setText(e.target.value)} maxLength={600}
            placeholder={kind === 'raise' ? tr('ui.meetingExtras.askWhenThePricingPage') : tr('ui.meetingExtras.donTAgreeToNew')}
            aria-label={tr('ui.meetingExtras.privateGuidance')} className="h-9 min-w-[180px] flex-1" />
          <Button type="submit" size="sm" disabled={busy || !text.trim()}>{tr('ui.meetingExtras.passOn')}</Button>
        </div>
        {guidance.length > 0 && (
          <ul className="list-disc pl-5 text-[12.5px] text-fg-dim">
            {guidance.map((g, i) => <li key={i}>{g}</li>)}
          </ul>
        )}
      </form>
      {sent && <p aria-live="polite" className="text-[12.5px] text-fg-dim">{sent}</p>}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
      {liveNotes && (
        <details open className="rounded-[10px] border border-line px-3 py-2">
          <summary className="cursor-pointer text-[12.5px] font-medium">{tr('ui.meetingExtras.runningNotes')}</summary>
          <div className="md mt-2 break-words text-[13px]"><MarkdownMessage content={liveNotes} /></div>
        </details>
      )}
    </div>
  );
}

/** What the agent saw when it couldn't get into the call: the controls it
 *  read and, when there is one, a screenshot. */
export function SeenOnJoin({ meetingId, seen, hasImage }: { meetingId: string; seen: string; hasImage: boolean }) {
  const [image, setImage] = useState('');

  useEffect(() => {
    if (!hasImage) return;
    let url = '';
    let gone = false;
    api.get(`/meetings/${meetingId}/seen.png`, { responseType: 'blob' })
      .then((r) => {
        if (gone) return;
        url = URL.createObjectURL(r.data as Blob);
        setImage(url);
      })
      .catch(() => {});
    return () => {
      gone = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [meetingId, hasImage]);

  if (!seen && !hasImage) return null;
  return (
    <details className="mb-3 rounded-[10px] border border-line px-3 py-2">
      <summary className="cursor-pointer text-[12.5px] font-medium">{tr('ui.meetingExtras.whatTheAgentSaw')}</summary>
      {image && <img src={image} alt={tr('ui.meetingExtras.theAgentSScreenWhen')} className="mt-2 max-h-[360px] rounded border border-line" />}
      {seen && (
        <pre className="mt-2 max-h-[240px] overflow-auto whitespace-pre-wrap text-[11.5px] leading-relaxed text-fg-dim">{seen}</pre>
      )}
    </details>
  );
}

/** Search across past meetings' notes and transcripts. */
export function MeetingSearch({ onOpen }: { onOpen: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    const q = query.trim();
    if (!q) {
      setHits(null);
      return;
    }
    setBusy(true);
    try {
      const res = await api.get<{ results: SearchHit[] }>('/meetings/search', { params: { q } });
      setHits(res.data.results);
    } catch {
      setHits([]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <form onSubmit={(e) => void search(e)} role="search" className="flex gap-2">
        <Input value={query} onChange={(e) => { setQuery(e.target.value); if (!e.target.value) setHits(null); }}
          placeholder={tr('ui.meetingExtras.searchMeetingsATopicA')} aria-label={tr('ui.meetingExtras.searchMeetings')} className="h-9 flex-1" />
        <Button type="submit" size="sm" variant="outline" disabled={busy || !query.trim()}>{tr('ui.meetingExtras.search')}</Button>
      </form>
      {hits && (
        hits.length === 0 ? <p className="text-[12.5px] text-fg-dim">{tr('ui.meetingExtras.noMeetingsMatchThat')}</p> : (
          <ul className="divide-y divide-line rounded-[10px] border border-line bg-panel">
            {hits.map((h) => (
              <li key={h.id}>
                <button type="button" onClick={() => onOpen(h.id)} className="flex w-full flex-col gap-0.5 px-3 py-2 text-left hover:bg-panel-2">
                  <span className="text-[13px] font-medium">{h.title} <span className="font-mono text-[11px] font-normal text-fg-dim">{h.date}</span></span>
                  <span className="text-[12.5px] text-fg-dim">{h.snippet}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}

/** How agents behave in every call: what answers may use, what to watch
 *  for, words to expect, speakers and running notes. */
export function MeetingSettingsPanel({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState<MeetingSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get<MeetingSettings>('/meetings/settings').then((r) => setForm(r.data))
      .catch((e) => setError(errorMessage(e, tr('ui.meetingExtras.couldNotLoadTheMeeting'))));
  }, []);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.put<MeetingSettings>('/meetings/settings', form);
      setForm(res.data);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetingExtras.couldNotSaveTheMeeting')));
    } finally {
      setBusy(false);
    }
  };
  const set = <K extends keyof MeetingSettings>(key: K, value: MeetingSettings[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  return (
    <form onSubmit={(e) => void save(e)} className="flex flex-col gap-3 rounded-[10px] border border-line bg-panel px-4 py-3.5">
      <p className="text-[14px] font-medium">{tr('ui.meetingExtras.meetingSettings')}</p>
      {!form ? <p className="text-[13px] text-fg-dim">{error || tr('ui.meetingExtras.loading')}</p> : (
        <>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
            {tr('ui.meetingExtras.whenSomeoneAsksTheAgent')}
            <Select value={form.share} onChange={(e) => set('share', e.target.value as ShareLevel)} className="h-9">
              <option value="call">{tr('ui.meetingExtras.onlyWhatWasSaidIn')}</option>
              <option value="work">{tr('ui.meetingExtras.alsoYourMemoryKnowledgePast')}</option>
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
            {tr('ui.meetingExtras.alertMeRightAwayWhen')}
            <Textarea value={form.watch_words} onChange={(e) => set('watch_words', e.target.value)} rows={2} maxLength={2000}
              placeholder={tr('ui.meetingExtras.budgetMyNameLaunchDate')} />
          </label>
          <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
            {tr('ui.meetingExtras.namesAndWordsSpeechTo')}
            <Textarea value={form.vocabulary} onChange={(e) => set('vocabulary', e.target.value)} rows={2} maxLength={2000}
              placeholder={tr('ui.meetingExtras.akosuaKubernetesCognitrix')} />
          </label>
          <label className="flex items-center gap-2 text-[12.5px]">
            <input type="checkbox" checked={form.speaker_pass} onChange={(e) => set('speaker_pass', e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]" />
            {tr('ui.meetingExtras.afterTheCallTellThe')}
          </label>
          <label className="flex items-center gap-2 text-[12.5px]">
            <input type="checkbox" checked={form.live_notes} onChange={(e) => set('live_notes', e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]" />
            {tr('ui.meetingExtras.keepRunningNotesDuringThe')}
          </label>
          {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={busy}>{saved ? tr('ui.meetingExtras.saved') : tr('ui.meetingExtras.save')}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={onClose}>{tr('ui.meetingExtras.close')}</Button>
          </div>
        </>
      )}
    </form>
  );
}

/** The list of tabs a finished meeting shows. */
export function MeetingTabs<T extends string>({ tabs, value, onChange, labels }: {
  tabs: readonly T[]; value: T; onChange: (tab: T) => void; labels: Record<T, string>;
}) {
  return (
    <div role="tablist" aria-label={tr('ui.meetingExtras.meetingView')} className="flex rounded-lg border border-line p-0.5 text-[12.5px]">
      {tabs.map((name) => (
        <button key={name} type="button" role="tab" aria-selected={value === name} onClick={() => onChange(name)}
          className={cn('min-h-9 rounded-md px-3 md:min-h-7', value === name ? 'bg-panel-2 text-fg' : 'text-fg-dim hover:text-fg')}>
          {labels[name]}
        </button>
      ))}
    </div>
  );
}
