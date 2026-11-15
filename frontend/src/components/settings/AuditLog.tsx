import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { Group, StatusPill, buttonClass } from '@/components/settings/ui';
import { tr, dateLocale } from '@/lib/i18n';

export interface AuditEvent {
  id: string;
  at: number;
  kind: 'tool' | 'approval' | 'sign_in' | 'people' | 'settings';
  action: string;
  user_id: string;
  actor: string;
  agent_id: string;
  agent_name: string;
  target: string;
  detail: string;
  outcome: string;
  risk: string;
}

interface AuditPage {
  events: AuditEvent[];
  next_before: string | null;
  retention_days: number;
  admin: boolean;
  people?: { id: string; name: string; email: string }[];
}

const KINDS: { value: string; label: string }[] = [
  { value: '', get label() { return tr('ui.settings.auditLog.everything'); } },
  { value: 'tool', get label() { return tr('ui.settings.auditLog.agentActions'); } },
  { value: 'people', get label() { return tr('ui.settings.auditLog.peopleChanges'); } },
  { value: 'sign_in', label: 'Sign-ins' },
  { value: 'settings', get label() { return tr('ui.settings.auditLog.accountChanges'); } },
];

const OUTCOMES: { value: string; label: string }[] = [
  { value: '', get label() { return tr('ui.settings.auditLog.anyResult'); } },
  { value: 'ok', get label() { return tr('ui.settings.auditLog.done'); } },
  { value: 'failed', get label() { return tr('ui.settings.auditLog.failed'); } },
  { value: 'denied', get label() { return tr('ui.settings.auditLog.refused'); } },
  { value: 'blocked', get label() { return tr('ui.settings.auditLog.blocked'); } },
];

const selectClass =
  'h-10 min-w-0 rounded-lg border border-line bg-panel-2 px-2.5 text-[13px] outline-none focus:border-accent md:h-9';

const OUTCOME_PILL: Record<string, { tone: 'ok' | 'warn' | 'off' | 'accent'; label: string }> = {
  ok: { tone: 'ok', label: 'done' },
  failed: { tone: 'warn', label: 'failed' },
  denied: { tone: 'warn', label: 'refused' },
  blocked: { tone: 'warn', label: 'blocked' },
};

function dayLabel(at: number) {
  const date = new Date(at * 1000);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString(dateLocale(), { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

function timeLabel(at: number) {
  return new Date(at * 1000).toLocaleTimeString(dateLocale(), { hour: '2-digit', minute: '2-digit' });
}

/** What happened, in words. */
function sentence(event: AuditEvent) {
  const who = event.actor || 'Someone';
  if (event.kind === 'tool') {
    return (
      <>
        <span className="font-medium">{event.agent_name || tr('ui.settings.auditLog.anAgent')}</span>
        <span className="text-fg-dim">{' '}{tr('ui.settings.auditLog.used')}{' '}</span>
        <span className="font-medium">{event.action}</span>
        <span className="text-fg-dim">{' '}{tr('ui.settings.auditLog.forWho', { who })}</span>
      </>
    );
  }
  return (
    <>
      <span className="font-medium">{who}</span>
      <span className="text-fg-dim"> · </span>
      {event.action}
      {event.target && <span className="font-medium"> {event.target}</span>}
    </>
  );
}

/** Who did what on this server. Admins see everyone; others see their own. */
export function AuditLog() {
  const [kind, setKind] = useState('');
  const [outcome, setOutcome] = useState('');
  const [person, setPerson] = useState('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState<AuditPage | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (before = '') => {
    setLoading(true);
    setError('');
    try {
      const { data } = await api.get<AuditPage>('/audit', {
        params: { kind, outcome, person, q: search, before, limit: 100 },
      });
      setPage(data);
      setEvents((list) => (before ? [...list, ...data.events] : data.events));
    } catch (err) {
      setError(errorMessage(err, tr('ui.settings.auditLog.couldNotLoadTheAudit')));
    } finally {
      setLoading(false);
    }
  }, [kind, outcome, person, search]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const groups = useMemo(() => {
    const out: { day: string; rows: AuditEvent[] }[] = [];
    for (const event of events) {
      const day = dayLabel(event.at);
      const last = out[out.length - 1];
      if (last && last.day === day) last.rows.push(event);
      else out.push({ day, rows: [event] });
    }
    return out;
  }, [events]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label={tr('ui.settings.auditLog.filterTheAuditLog')}>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={tr('ui.settings.auditLog.searchActionsAgentsDetails')}
          aria-label={tr('ui.settings.auditLog.searchTheAuditLog')}
          className={`${selectClass} flex-1 basis-[200px] px-3`}
        />
        {page?.admin && (
          <select aria-label={tr('ui.settings.auditLog.person')} value={person} onChange={(e) => setPerson(e.target.value)} className={selectClass}>
            <option value="">{tr('ui.settings.auditLog.everyone')}</option>
            {(page.people ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.name || p.email}</option>
            ))}
          </select>
        )}
        <select aria-label={tr('ui.settings.auditLog.kind')} value={kind} onChange={(e) => setKind(e.target.value)} className={selectClass}>
          {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
        <select aria-label={tr('ui.settings.auditLog.result')} value={outcome} onChange={(e) => setOutcome(e.target.value)} className={selectClass}>
          {OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      {error && <p role="alert" className="mb-3 text-[12.5px] text-danger-ink">{error}</p>}
      {!page && !error && <p className="text-[13px] text-fg-dim">{tr('ui.settings.auditLog.loading')}</p>}
      {page && events.length === 0 && !loading && (
        <p className="rounded-[12px] border border-line bg-panel p-3.5 text-[13px] text-fg-dim">
          {page.next_before
            ? tr('ui.settings.auditLog.nothingMatchesInThisStretch')
            : tr('ui.settings.auditLog.nothingMatchesYetAgentActions')}
        </p>
      )}

      {groups.map((group) => (
        <section key={group.day} className="mb-4" aria-label={group.day}>
          <h3 className="mb-1.5 font-mono text-[10.5px] tracking-[0.12em] text-fg-dim">{group.day.toUpperCase()}</h3>
          <Group>
            {group.rows.map((event) => {
              const pill = OUTCOME_PILL[event.outcome];
              return (
                <div key={event.id} className="flex gap-3 px-3.5 py-2.5">
                  <time className="w-[64px] shrink-0 whitespace-nowrap pt-px font-mono text-[11px] tabular-nums text-fg-dim" dateTime={new Date(event.at * 1000).toISOString()}>
                    {timeLabel(event.at)}
                  </time>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
                      <span className="min-w-0">{sentence(event)}</span>
                      {pill && event.outcome !== 'ok' && <StatusPill tone={pill.tone}>{pill.label}</StatusPill>}
                      {event.risk && event.risk !== 'low' && event.risk !== 'safe' && (
                        <span className="font-mono text-[10.5px] text-fg-dim">{tr('ui.settings.auditLog.riskRisk', { risk: event.risk })}</span>
                      )}
                    </div>
                    {event.detail && (
                      <div className="mt-0.5 break-words font-mono text-[11.5px] leading-relaxed text-fg-dim">{event.detail}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </Group>
        </section>
      ))}

      {page?.next_before && (
        <button type="button" className={buttonClass} disabled={loading} onClick={() => void load(page.next_before ?? '')}>
          {loading ? tr('ui.settings.auditLog.loading') : tr('ui.settings.auditLog.showOlder')}
        </button>
      )}
      {page && (
        <p className="mt-4 max-w-[62ch] text-[12px] leading-relaxed text-fg-dim">
          {tr('ui.settings.auditLog.valueFieldsThatHoldKeys', { value: page.admin
            ? tr('ui.settings.auditLog.youSeeEveryoneOnThis', { retention_days: page.retention_days })
            : tr('ui.settings.auditLog.theseAreYourOwnEntries', { retention_days: page.retention_days }) })}
        </p>
      )}
    </div>
  );
}
