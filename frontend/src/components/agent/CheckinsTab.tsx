import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { timeAgo, timeUntil, type TabProps } from '@/lib/agentDraft';
import { Card, Check, Pill, SettingLine, buttonClass, inputClass, primaryButtonClass } from '@/components/agent/ui';
import { cn } from '@/lib/utils';

type BriefKey = 'keep_true' | 'sources' | 'tell_me' | 'not_yet';

interface Offer {
  id: string;
  title: string;
  details: string;
}

interface Standing {
  enabled: boolean;
  brief: Record<BriefKey, string>;
  every_minutes: number;
  offers: boolean;
  primary: boolean;
  task_id: string | null;
  notes: string;
  next_check_at: string | null;
  wake_reason: string | null;
  last_run: { id: string; status: string; completed_at: string | null; result: string } | null;
  pending_offers: Offer[];
  limits: { min_every_minutes: number; max_every_minutes: number; brief_max_chars: number };
}

const BRIEF: { key: BriefKey; label: string; hint: string; placeholder: string }[] = [
  { key: 'keep_true', label: 'What should stay true', hint: 'The outcome you want kept up, not a one-off job.', placeholder: 'Every client email gets a reply within one working day.' },
  { key: 'sources', label: 'Where to look', hint: 'Apps, folders, pages or chats to read on each check-in.', placeholder: 'My Gmail inbox and the "Clients" folder.' },
  { key: 'tell_me', label: 'What I want to hear about', hint: 'When it should alert you. Everything else stays quiet.', placeholder: 'Anything from Acme, and any mail waiting longer than a day.' },
  { key: 'not_yet', label: "What it must not do yet", hint: 'Limits that hold even after you accept an offer.', placeholder: 'Never send mail. Drafts only.' },
];

const EVERY = [
  { value: 30, label: 'Every 30 minutes' },
  { value: 60, label: 'Every hour' },
  { value: 240, label: 'Every 4 hours' },
  { value: 720, label: 'Twice a day' },
  { value: 1440, label: 'Once a day' },
  { value: 10080, label: 'Once a week' },
];

const textareaClass = 'min-h-[76px] w-full resize-y rounded border border-line bg-bg px-3 py-2 text-[14px] leading-relaxed text-fg placeholder:text-fg-dim focus:border-accent';

/** A standing assistant: a brief the agent checks in on by itself. Saved
 *  on its own (not with the page's Save), because it starts and stops work. */
export default function CheckinsTab({ agent, refreshOverview }: TabProps) {
  const ids = { every: useId(), notes: useId() };
  const { data, loading, error, refetch, setData } = useResource<Standing>(`/agents/${agent.id}/standing`);
  const [form, setForm] = useState<Standing | null>(null);
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  if (!form) {
    return (
      <Card>
        <p role={error ? 'alert' : 'status'} className={cn('text-[13.5px]', error ? 'text-danger-ink' : 'text-fg-dim')}>
          {error ? `Couldn't load check-ins: ${error}` : loading ? 'Loading…' : ''}
        </p>
        {error && <button type="button" onClick={() => refetch()} className={cn(buttonClass, 'mt-3')}>Try again</button>}
      </Card>
    );
  }

  const set = (patch: Partial<Standing>) => setForm({ ...form, ...patch });
  const hasBrief = BRIEF.some((b) => form.brief[b.key].trim());
  const dirty = !!data && JSON.stringify({ ...data, pending_offers: 0, last_run: 0 }) !== JSON.stringify({ ...form, pending_offers: 0, last_run: 0 });

  const save = async (patch: Partial<Standing> = {}) => {
    const next = { ...form, ...patch };
    setBusy('save');
    setNote(null);
    try {
      const { data: saved } = await api.put<Standing>(`/agents/${agent.id}/standing`, {
        enabled: next.enabled,
        brief: next.brief,
        every_minutes: next.every_minutes,
        offers: next.offers,
        primary: next.primary,
        ...(next.task_id ? { notes: next.notes } : {}),
      });
      setData(saved);
      setForm(saved);
      setNote({ text: saved.enabled ? 'Saved. Check-ins are on.' : 'Saved.' });
      refreshOverview();
    } catch (e) {
      setNote({ text: errorMessage(e, "Couldn't save."), bad: true });
    } finally {
      setBusy('');
    }
  };

  const checkNow = async () => {
    setBusy('check');
    setNote(null);
    try {
      await api.post(`/agents/${agent.id}/standing/check`);
      setNote({ text: 'Check-in started. Anything it finds shows up in your Inbox.' });
      refreshOverview();
    } catch (e) {
      setNote({ text: errorMessage(e, "Couldn't start a check-in."), bad: true });
    } finally {
      setBusy('');
    }
  };

  const everyOptions = EVERY.some((o) => o.value === form.every_minutes)
    ? EVERY
    : [...EVERY, { value: form.every_minutes, label: `Every ${form.every_minutes} minutes` }].sort((a, b) => a.value - b.value);

  return (
    <div className="flex flex-col gap-[18px]">
      <Card
        title="Standing brief"
        aside={form.enabled ? <Pill tone="ok">check-ins on</Pill> : <Pill>check-ins off</Pill>}
      >
        <p className="-mt-2 text-[13px] leading-relaxed text-fg-dim">
          Give {agent.name} something to keep an eye on. It checks in by itself, keeps notes between check-ins, and comes to you only when something needs you.
          A check-in can read and look things up but cannot send, change or delete anything.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          {BRIEF.map((b) => (
            <label key={b.key} className="flex flex-col gap-1.5">
              <span className="text-[13.5px] font-medium">{b.label}</span>
              <textarea
                value={form.brief[b.key]}
                maxLength={form.limits.brief_max_chars}
                onChange={(e) => set({ brief: { ...form.brief, [b.key]: e.target.value } })}
                placeholder={b.placeholder}
                className={textareaClass}
              />
              <span className="text-[12px] text-fg-dim">{b.hint}</span>
            </label>
          ))}
        </div>

        <div className="mt-5 flex flex-col gap-4 border-t border-line pt-4">
          <SettingLine label="How often" detail="It can also plan an extra check itself when it expects news sooner.">
            <label htmlFor={ids.every} className="sr-only">How often to check in</label>
            <select id={ids.every} value={form.every_minutes} onChange={(e) => set({ every_minutes: Number(e.target.value) })} className={inputClass}>
              {everyOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </SettingLine>
          <Check
            checked={form.offers}
            onChange={(v) => set({ offers: v })}
            label="Offer to take on work it notices"
            detail="Offers land in your Inbox with Accept and Dismiss. Nothing is done until you accept, and dismissed offers aren't made again."
          />
          <Check
            checked={form.primary}
            onChange={(v) => set({ primary: v })}
            label="This is my main assistant"
            detail="The dashboard's ask bar and your chat apps start with this agent unless you pick another."
          />
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2.5 border-t border-line pt-4">
          {form.enabled ? (
            <>
              <button type="button" disabled={!!busy || !dirty} onClick={() => void save()} className={primaryButtonClass}>
                {busy === 'save' ? 'Saving…' : 'Save'}
              </button>
              <button type="button" disabled={!!busy || dirty} onClick={() => void checkNow()} className={buttonClass}
                title={dirty ? 'Save your changes first' : undefined}>
                {busy === 'check' ? 'Starting…' : 'Check in now'}
              </button>
              <button type="button" disabled={!!busy} onClick={() => void save({ enabled: false })} className={cn(buttonClass, 'border-danger/40 text-danger-ink')}>
                Turn off check-ins
              </button>
            </>
          ) : (
            <>
              <button type="button" disabled={!!busy || !hasBrief} onClick={() => void save({ enabled: true })} className={primaryButtonClass}>
                {busy === 'save' ? 'Saving…' : 'Turn on check-ins'}
              </button>
              {dirty && (
                <button type="button" disabled={!!busy} onClick={() => void save()} className={buttonClass}>Save without turning on</button>
              )}
            </>
          )}
          <p role="status" className={cn('min-w-[180px] flex-1 text-[13px]', note?.bad ? 'text-danger-ink' : 'text-fg-dim')}>
            {note?.text || (!form.enabled && !hasBrief ? 'Write at least one part of the brief to turn check-ins on.' : '')}
          </p>
        </div>
      </Card>

      {form.task_id && (
        <div className="grid grid-cols-1 gap-[18px] md:grid-cols-2">
          <Card title="Status">
            <dl className="-mt-1 text-[14px]">
              <div className="flex gap-3 border-b border-line py-2.5">
                <dt className="w-[110px] flex-none text-fg-dim">Next check-in</dt>
                <dd className="min-w-0 flex-1">
                  {form.next_check_at ? timeUntil(form.next_check_at) : 'Not planned'}
                  {form.wake_reason && <span className="block text-[12.5px] text-fg-dim">It asked to look again: {form.wake_reason}</span>}
                </dd>
              </div>
              <div className="flex gap-3 py-2.5">
                <dt className="w-[110px] flex-none text-fg-dim">Last check-in</dt>
                <dd className="min-w-0 flex-1">
                  {form.last_run ? (
                    <>
                      <span className={cn(form.last_run.status === 'failed' && 'text-danger-ink')}>
                        {form.last_run.status}{form.last_run.completed_at ? ` · ${timeAgo(form.last_run.completed_at)}` : ''}
                      </span>
                      {form.last_run.result && <span className="mt-1 line-clamp-4 block whitespace-pre-wrap text-[12.5px] text-fg-dim">{form.last_run.result}</span>}
                    </>
                  ) : 'None yet'}
                </dd>
              </div>
            </dl>
            <Link to={`/tasks/${form.task_id}`} className="mt-2 inline-block text-[13px] text-accent-ink hover:underline">See every check-in</Link>
          </Card>

          <Card title="Its notes" aside={form.pending_offers.length > 0 ? <Link to="/inbox" className="text-[13px] text-accent-ink hover:underline">{form.pending_offers.length} {form.pending_offers.length === 1 ? 'offer' : 'offers'} waiting</Link> : undefined}>
            <label htmlFor={ids.notes} className="-mt-2 block text-[13px] text-fg-dim">
              What it remembers between check-ins. You can read and correct it.
            </label>
            <textarea id={ids.notes} value={form.notes} maxLength={8000} onChange={(e) => set({ notes: e.target.value })}
              placeholder="Nothing yet. It writes here after its first check-in."
              className={cn(textareaClass, 'mt-2.5 min-h-[132px] font-mono text-[12.5px]')} />
            {data && form.notes !== data.notes && (
              <button type="button" disabled={!!busy} onClick={() => void save()} className={cn(buttonClass, 'mt-2.5')}>Save notes</button>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
