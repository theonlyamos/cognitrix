import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Search } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Select } from '@/lib/components/ui/select';
import { Textarea } from '@/lib/components/ui/textarea';
import { Sheet } from '@/components/meetings/Overlay';
import { CalendarRulesFields, type Calendar, type CalendarRules } from '@/components/UpcomingCalls';
import type { AgentLite, ShareLevel } from '@/components/meetings/types';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

export type { ShareLevel } from '@/components/meetings/types';

export interface MeetingSettings {
  share: ShareLevel;
  watch_words: string;
  vocabulary: string;
  speaker_pass: boolean;
  live_notes: boolean;
}

interface SearchHit { id: string; title: string; date: string; kind: string; snippet: string }

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
    <section aria-label={tr('ui.meetingExtras.whatTheAgentSaw')} className="flex flex-col gap-2">
      <h3 className="text-[11px] font-medium uppercase tracking-[.08em] text-fg-dim">{tr('ui.meetingExtras.whatTheAgentSaw')}</h3>
      <div className="flex flex-col gap-3 lg:flex-row">
        {image && <img src={image} alt={tr('ui.meetingExtras.theAgentSScreenWhen')} className="max-h-[320px] min-w-0 rounded-lg border border-line object-contain lg:max-w-[60%]" />}
        {seen && (
          <pre className="max-h-[320px] min-w-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg px-3 py-2 text-[11.5px] leading-relaxed text-fg-dim">{seen}</pre>
        )}
      </div>
    </section>
  );
}

/** Search across past meetings' notes and transcripts; hits drop down under the box. */
export function MeetingSearch({ onOpen, className }: { onOpen: (id: string) => void; className?: string }) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hits) return;
    const away = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setHits(null); };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [hits]);

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
    <div ref={box} className={cn('relative', className)}>
      <form onSubmit={(e) => void search(e)} role="search" className="relative">
        <Search size={14} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-dim" />
        <input type="search" value={query} aria-label={tr('ui.meetingExtras.searchMeetings')}
          placeholder={tr('ui.meetingExtras.searchNotesAndTranscripts')}
          onChange={(e) => { setQuery(e.target.value); if (!e.target.value) setHits(null); }}
          onKeyDown={(e) => { if (e.key === 'Escape') setHits(null); }}
          className="h-10 w-full rounded-lg border border-line bg-panel pl-8 pr-16 text-[13px] outline-none placeholder:text-fg-dim focus:border-fg-dim md:h-8" />
        <button type="submit" disabled={busy || !query.trim()}
          className="absolute right-1 top-1/2 h-8 -translate-y-1/2 rounded-md px-2 text-[12px] text-fg-dim hover:bg-panel-2 hover:text-fg disabled:opacity-0 md:h-6">
          {tr('ui.meetingExtras.search')}
        </button>
      </form>
      {hits && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1 max-h-[60vh] overflow-y-auto rounded-xl border border-line bg-panel p-1 shadow-xl md:min-w-[420px]">
          {hits.length === 0 ? <p className="px-3 py-2 text-[12.5px] text-fg-dim">{tr('ui.meetingExtras.noMeetingsMatchThat')}</p> : (
            <ul>
              {hits.map((h) => (
                <li key={h.id}>
                  <button type="button" onClick={() => { setHits(null); onOpen(h.id); }} className="flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left hover:bg-panel-2">
                    <span className="text-[13px] font-medium">{h.title} <span className="font-mono text-[11px] font-normal text-fg-dim">{h.date}</span></span>
                    <span className="text-[12.5px] text-fg-dim">{h.snippet}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-line pt-4 first:border-t-0 first:pt-1">
      <h3 className="text-[11px] font-medium uppercase tracking-[.08em] text-fg-dim">{title}</h3>
      {children}
    </section>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-3">
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-medium">{label}</span>
        <span className="block text-[12px] text-fg-dim">{hint}</span>
      </span>
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 h-4 w-4 flex-none accent-[var(--accent)]" />
    </label>
  );
}

/** Everything about meetings in one place: how agents behave in every call,
 *  the calendar rules, and who writes the notes for uploads. */
export function MeetingSettingsSheet({ open, onClose, calendar, agents, uploadAgent, onUploadAgent }: {
  open: boolean;
  onClose: () => void;
  calendar: Calendar;
  agents: AgentLite[];
  uploadAgent: string;
  onUploadAgent: (id: string) => void;
}) {
  const [form, setForm] = useState<MeetingSettings | null>(null);
  const [rules, setRules] = useState<CalendarRules | null>(null);
  const [writer, setWriter] = useState(uploadAgent);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setError('');
    setWriter(uploadAgent);
    api.get<MeetingSettings>('/meetings/settings').then((r) => setForm(r.data))
      .catch((e) => setError(errorMessage(e, tr('ui.meetingExtras.couldNotLoadTheMeeting'))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const calendarSettings = calendar.data?.settings;
  useEffect(() => {
    if (!open || !calendarSettings) return;
    const { enabled, agent_id, auto_join, never_words, remind_minutes } = calendarSettings;
    setRules({ enabled, agent_id, auto_join, never_words, remind_minutes });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, !!calendarSettings]);

  const save = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!form) return;
    setBusy(true);
    setError('');
    try {
      await api.put<MeetingSettings>('/meetings/settings', form);
      if (rules && calendar.data?.connected) {
        const before = calendar.data.settings;
        const changed = (Object.keys(rules) as (keyof CalendarRules)[]).some((k) => rules[k] !== before[k]);
        if (changed && !(await calendar.save(rules))) {
          setError(tr('ui.upcomingCalls.couldNotSaveTheSettings'));
          return;
        }
      }
      onUploadAgent(writer);
      onClose();
    } catch (e) {
      setError(errorMessage(e, tr('ui.meetingExtras.couldNotSaveTheMeeting')));
    } finally {
      setBusy(false);
    }
  };
  const set = <K extends keyof MeetingSettings>(key: K, value: MeetingSettings[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  return (
    <Sheet open={open} title={tr('ui.meetingExtras.meetingSettings')} onClose={onClose}
      footer={(
        <>
          {error && <p role="alert" className="mr-auto text-[12.5px] text-danger-ink">{error}</p>}
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>{tr('ui.meetings.cancel')}</Button>
          <Button type="button" size="sm" disabled={busy || !form} onClick={() => void save()}>{tr('ui.meetingExtras.save')}</Button>
        </>
      )}>
      {!form ? <p className="py-2 text-[13px] text-fg-dim">{error || tr('ui.meetingExtras.loading')}</p> : (
        <form onSubmit={(e) => void save(e)} className="flex flex-col gap-5">
          <Section title={tr('ui.meetingExtras.inEveryCall')}>
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1.5 text-[12px] text-fg-dim">{tr('ui.meetingExtras.whenSomeoneAsksTheAgent')}</legend>
              <Choice name="share" value={form.share} onChange={(v) => set('share', v)} options={[
                { id: 'call', label: tr('ui.meetingExtras.onlyTheCall') },
                { id: 'work', label: tr('ui.meetingExtras.alsoMyMemoryAndPast') },
              ]} />
            </fieldset>
            <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
              {tr('ui.meetingExtras.alertMeRightAwayWhen')}
              <Textarea value={form.watch_words} onChange={(e) => set('watch_words', e.target.value)} rows={2} maxLength={2000}
                placeholder={tr('ui.meetingExtras.budgetMyNameLaunchDate')} />
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-fg-dim">
              {tr('ui.meetingExtras.namesAndWordsSpeechTo')}
              <Textarea value={form.vocabulary} onChange={(e) => set('vocabulary', e.target.value)} rows={2} maxLength={2000}
                placeholder={tr('ui.meetingExtras.akosuaKubernetesCognitrix')} />
            </label>
            <Toggle label={tr('ui.meetingExtras.runningNotes')} hint={tr('ui.meetingExtras.notesBuildUpWhileThe')}
              checked={form.live_notes} onChange={(v) => set('live_notes', v)} />
            <Toggle label={tr('ui.meetingExtras.tellTheSpeakersApart')} hint={tr('ui.meetingExtras.afterTheCallWithGoogle')}
              checked={form.speaker_pass} onChange={(v) => set('speaker_pass', v)} />
          </Section>
          <Section title={tr('ui.meetingExtras.calendar')}>
            {rules ? <CalendarRulesFields calendar={calendar} agents={agents} rules={rules} onChange={setRules} />
              : <p className="text-[12.5px] text-fg-dim">{tr('ui.meetingExtras.loading')}</p>}
          </Section>
          <Section title={tr('ui.meetingExtras.recordings')}>
            <label className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="flex-1">{tr('ui.meetingExtras.notesForUploadsWrittenBy')}</span>
              <Select value={writer} onChange={(e) => setWriter(e.target.value)} aria-label={tr('ui.meetings.agentForNewRecordings')} className="h-9 w-auto min-w-[170px]">
                <option value="">{tr('ui.meetings.yourMainAgent')}</option>
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </label>
          </Section>
          <button type="submit" hidden />
        </form>
      )}
    </Sheet>
  );
}

/** A row of choices that look like one switch (radio buttons underneath). */
export function Choice<T extends string>({ name, value, onChange, options, disabled }: {
  name: string; value: T; onChange: (value: T) => void; options: { id: T; label: string }[]; disabled?: T[];
}) {
  return (
    <div className="flex flex-wrap gap-0.5 rounded-lg border border-line bg-panel p-0.5">
      {options.map(({ id, label }) => (
        <label key={id} className={cn('flex min-h-9 flex-1 cursor-pointer items-center justify-center whitespace-nowrap rounded-md px-3 text-center text-[12.5px] md:min-h-7',
          value === id ? 'bg-panel-2 font-medium text-fg' : 'text-fg-dim hover:text-fg', disabled?.includes(id) && 'pointer-events-none opacity-50')}>
          <input type="radio" name={name} value={id} checked={value === id} disabled={disabled?.includes(id)}
            onChange={() => onChange(id)} className="sr-only" />
          {label}
        </label>
      ))}
    </div>
  );
}

/** Underlined tabs for a meeting's views. */
export function MeetingTabs<T extends string>({ tabs, value, onChange, labels, className }: {
  tabs: readonly T[]; value: T; onChange: (tab: T) => void; labels: Record<T, ReactNode>; className?: string;
}) {
  return (
    <div role="tablist" aria-label={tr('ui.meetingExtras.meetingView')} className={cn('flex gap-5 overflow-x-auto border-b border-line text-[13px]', className)}>
      {tabs.map((name) => (
        <button key={name} type="button" role="tab" aria-selected={value === name} onClick={() => onChange(name)}
          className={cn('-mb-px min-h-10 flex-none whitespace-nowrap border-b-2 pb-1.5 pt-2 md:min-h-9',
            value === name ? 'border-accent font-medium text-fg' : 'border-transparent text-fg-dim hover:text-fg')}>
          {labels[name]}
        </button>
      ))}
    </div>
  );
}
