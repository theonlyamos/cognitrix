import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { useResource } from '@/hooks/useResource';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { PageHeader, LoadingState, ErrorState } from '@/components/list-ui';
import { cn } from '@/lib/utils';
import { tr } from '@/lib/i18n';

type Status = 'pending' | 'approved' | 'paying' | 'denied' | 'expired' | 'paid' | 'failed' | 'cancelled' | 'unconfirmed';

export interface PaymentRequest {
  id: string;
  agent_id: string;
  agent_name: string;
  merchant: string;
  site: string;
  checkout_url: string;
  amount_cents: number;
  currency: string;
  amount: string;
  purpose: string;
  items: string;
  status: Status;
  card_id: string;
  approved_until: string;
  expires_at: string;
  result: string;
  created_at: string;
}
interface Card { id: string; label: string; brand: string; last4: string; exp_month: number; exp_year: number; is_default: boolean }
interface Settings { enabled: boolean; currency: string; per_payment_cents: number; monthly_cents: number; agent_ids: string[] }
interface State {
  settings: Settings;
  currencies: string[];
  cards: Card[];
  requests: PaymentRequest[];
  month_cents: number;
  approval_minutes: number;
}
interface AgentLite { id: string; name: string }

const STATUS: Record<Status, string> = {
  pending: 'waiting for you', approved: 'approved', paying: 'paying', denied: 'declined', expired: 'lapsed',
  paid: 'paid', failed: 'not paid', cancelled: 'called off', unconfirmed: 'check this',
};

function money(cents: number, currency: string): string {
  const value = currency === 'JPY' ? cents : cents / 100;
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

function when(stamp: string): string {
  const date = new Date(stamp.includes('T') ? stamp : `${stamp.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** One payment an agent asked for. Approving shows the total once more and
 *  sends it back, so the server can check it is the one the person saw. */
function RequestCard({ req, cards, highlight, onDone }: {
  req: PaymentRequest; cards: Card[]; highlight: boolean; onDone: () => void;
}) {
  const [cardId, setCardId] = useState(cards.find((c) => c.is_default)?.id ?? cards[0]?.id ?? '');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const act = async (work: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError('');
    try {
      await work();
      onDone();
    } catch (e) {
      setError(errorMessage(e, fallback));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <li className={cn('flex flex-col gap-2 rounded-[10px] border bg-panel px-4 py-3', highlight ? 'border-accent' : 'border-line')}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[20px] font-semibold tabular-nums">{money(req.amount_cents, req.currency)}</span>
        <span className="text-[14px]">{tr('ui.payments.to')}{' '}<b>{req.merchant}</b></span>
        <span className="font-mono text-[11.5px] text-fg-dim">{tr('ui.payments.onSite', { site: req.site })}</span>
      </div>
      <p className="text-[13.5px] leading-relaxed">{req.purpose}</p>
      {req.items && <pre className="whitespace-pre-wrap rounded-md bg-bg px-2.5 py-2 font-sans text-[12.5px] text-fg-dim">{req.items}</pre>}
      <p className="text-[12px] text-fg-dim">
        {tr('ui.payments.agentNameAskedWhenIf', { agent_name: req.agent_name || tr('ui.payments.anAgent'), when: when(req.created_at), site: req.site })}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {cards.length > 1 && (
          <Select value={cardId} onChange={(e) => setCardId(e.target.value)} aria-label={tr('ui.payments.cardToPayWith')} className="h-9 w-auto">
            {cards.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </Select>
        )}
        {confirming ? (
          <>
            <Button type="button" size="sm" disabled={busy}
              onClick={() => void act(() => api.post(`/payments/requests/${req.id}/approve`, { amount_cents: req.amount_cents, card_id: cardId }), tr('ui.payments.couldNotApproveThisPayment'))}>
              {tr('ui.payments.yesPayMoneyToMerchant', { money: money(req.amount_cents, req.currency), merchant: req.merchant })}
            </Button>
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>{tr('ui.payments.back')}</Button>
          </>
        ) : (
          <>
            <Button type="button" size="sm" disabled={busy || cards.length === 0} onClick={() => setConfirming(true)}>{tr('ui.payments.approve')}</Button>
            <Button type="button" size="sm" variant="outline" disabled={busy}
              onClick={() => void act(() => api.post(`/payments/requests/${req.id}/deny`), tr('ui.payments.couldNotDeclineThisPayment'))}>
              {tr('ui.payments.decline')}
            </Button>
          </>
        )}
      </div>
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </li>
  );
}

function AddCard({ onAdded }: { onAdded: () => void }) {
  const [form, setForm] = useState({ number: '', expiry: '', cvc: '', name: '', postal: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const match = /^\s*(\d{1,2})\s*\/\s*(\d{2,4})\s*$/.exec(form.expiry);
    if (!match) {
      setError(tr('ui.payments.writeTheExpiryAsMm'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api.post('/payments/cards', {
        number: form.number, exp_month: Number(match[1]), exp_year: Number(match[2]), cvc: form.cvc,
        name: form.name, postal: form.postal,
      });
      setForm({ number: '', expiry: '', cvc: '', name: '', postal: '' });
      onAdded();
    } catch (e) {
      setError(errorMessage(e, tr('ui.payments.couldNotSaveTheCard')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-2" autoComplete="off">
      <div className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr]">
        <Input value={form.number} onChange={set('number')} inputMode="numeric" placeholder={tr('ui.payments.cardNumber')} aria-label={tr('ui.payments.cardNumber')} className="h-9" required />
        <Input value={form.expiry} onChange={set('expiry')} placeholder="MM/YY" aria-label={tr('ui.payments.expiry')} className="h-9" required />
        <Input value={form.cvc} onChange={set('cvc')} inputMode="numeric" placeholder={tr('ui.payments.cvc')} aria-label={tr('ui.payments.securityCode')} className="h-9" required maxLength={4} />
      </div>
      <div className="grid gap-2 sm:grid-cols-[2fr_1fr]">
        <Input value={form.name} onChange={set('name')} placeholder={tr('ui.payments.nameOnCardOptional')} aria-label={tr('ui.payments.nameOnCard')} className="h-9" />
        <Input value={form.postal} onChange={set('postal')} placeholder={tr('ui.payments.zipPostalCodeOptional')} aria-label={tr('ui.payments.postalCode')} className="h-9" />
      </div>
      <p className="text-[12px] leading-relaxed text-fg-dim">
        {tr('ui.payments.savedEncryptedOnThisServer')}
      </p>
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
      <div><Button type="submit" size="sm" disabled={busy}>{busy ? tr('ui.payments.saving') : tr('ui.payments.saveCard')}</Button></div>
    </form>
  );
}

function SettingsCard({ state, agents, onSaved }: { state: State; agents: AgentLite[]; onSaved: () => void }) {
  const s = state.settings;
  const unit = (cents: number) => (s.currency === 'JPY' ? cents : cents / 100);
  const [perPayment, setPerPayment] = useState(String(unit(s.per_payment_cents)));
  const [monthly, setMonthly] = useState(String(unit(s.monthly_cents)));
  const [error, setError] = useState('');

  const save = async (body: Record<string, unknown>) => {
    setError('');
    try {
      await api.put('/payments/settings', body);
      onSaved();
    } catch (e) {
      setError(errorMessage(e, tr('ui.payments.couldNotSave')));
    }
  };
  // An empty saved list is from before agents were picked one by one: it meant all of them.
  const may = (id: string) => s.agent_ids.length === 0 || s.agent_ids.includes(id);
  const toggleAgent = (id: string) => {
    const now = agents.map((a) => a.id).filter(may);
    void save({ agent_ids: now.includes(id) ? now.filter((a) => a !== id) : [...now, id] });
  };

  return (
    <div className="flex flex-col gap-3 text-[13px]">
      <p className="text-[13.5px] leading-relaxed text-fg-dim">{tr('ui.payments.howItWorks')}</p>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={s.enabled} onChange={(e) => void save({ enabled: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
        <span><b>{tr('ui.payments.letAgentsAskToPay')}</b> <span className="text-fg-dim">{tr('ui.payments.everyPaymentStillWaitsFor')}</span></span>
      </label>
      {s.enabled && (
      <>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
          {tr('ui.payments.currency')}
          <Select value={s.currency} onChange={(e) => void save({ currency: e.target.value })} className="h-9">
            {state.currencies.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
          {tr('ui.payments.mostForOnePayment')}
          <Input value={perPayment} onChange={(e) => setPerPayment(e.target.value)} onBlur={() => void save({ per_payment: Number(perPayment) || 0 })} inputMode="decimal" className="h-9" />
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-fg-dim">
          {tr('ui.payments.mostInAMonth')}
          <Input value={monthly} onChange={(e) => setMonthly(e.target.value)} onBlur={() => void save({ monthly: Number(monthly) || 0 })} inputMode="decimal" className="h-9" />
        </label>
      </div>
      <p className="text-[12px] text-fg-dim">
        {tr('ui.payments.thisMonthMoneyOfMoney2', { money: money(state.month_cents, s.currency), money2: money(s.monthly_cents, s.currency) })}
      </p>
      {agents.length > 0 && (
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-[12.5px] text-fg-dim">{tr('ui.payments.agentsThatMayAsk')}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {agents.map((a) => (
              <label key={a.id} className="flex items-center gap-1.5">
                <input type="checkbox" checked={may(a.id)} onChange={() => toggleAgent(a.id)} className="h-4 w-4 accent-[var(--accent)]" />
                {a.name}
              </label>
            ))}
          </div>
          {!agents.some((a) => may(a.id)) && <p className="mt-1 text-[12.5px] text-fg-dim">{tr('ui.payments.tickAnAgent')}</p>}
        </fieldset>
      )}
      </>
      )}
      {error && <p role="alert" className="text-[12.5px] text-danger-ink">{error}</p>}
    </div>
  );
}

export default function Payments() {
  const { data, loading, error, refetch } = useResource<State>('/payments');
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [params] = useSearchParams();
  const focus = params.get('request');

  useEffect(() => {
    api.get<AgentLite[]>('/agents').then((r) => setAgents((r.data ?? []).filter((a) => a.id))).catch(() => {});
  }, []);

  // Waiting requests come in from agents while the page is open.
  useEffect(() => {
    const timer = window.setInterval(() => refetch({ silent: true }), 5000);
    return () => window.clearInterval(timer);
  }, [refetch]);

  const removeCard = async (card: Card) => {
    if (!window.confirm(tr('ui.payments.removeLabel', { label: card.label }))) return;
    try {
      await api.delete(`/payments/cards/${card.id}`);
    } finally {
      refetch({ silent: true });
    }
  };

  const waiting = data?.requests.filter((r) => r.status === 'pending') ?? [];
  const history = data?.requests.filter((r) => r.status !== 'pending') ?? [];

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-bg text-fg">
      <PageHeader title={tr('ui.payments.payments')} subtitle={tr('ui.payments.agentsPayAtACheckout')} />
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <LoadingState label={tr('ui.payments.loadingPayments')} />
        ) : error || !data ? (
          <ErrorState message={error || tr('ui.payments.couldNotLoadPayments')} onRetry={refetch} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-5 md:px-6">
            {waiting.length > 0 && (
              <section aria-label={tr('ui.payments.waitingForYou')} className="flex flex-col gap-2">
                <h2 className="font-mono text-[11px] tracking-wide text-fg-dim">{tr('ui.payments.waitingForYou_5453')}</h2>
                <ul className="flex flex-col gap-2">
                  {waiting.map((r) => (
                    <RequestCard key={r.id} req={r} cards={data.cards} highlight={r.id === focus} onDone={() => refetch({ silent: true })} />
                  ))}
                </ul>
              </section>
            )}
            <section className="flex flex-col gap-2">
              <h2 className="font-mono text-[11px] tracking-wide text-fg-dim">{tr('ui.payments.settings')}</h2>
              <div className="rounded-[10px] border border-line bg-panel px-4 py-3.5">
                <SettingsCard key={JSON.stringify(data.settings)} state={data} agents={agents} onSaved={() => refetch({ silent: true })} />
              </div>
            </section>
            {(data.settings.enabled || data.cards.length > 0) && (
            <section className="flex flex-col gap-2">
              <h2 className="font-mono text-[11px] tracking-wide text-fg-dim">{tr('ui.payments.cards')}</h2>
              <div className="flex flex-col gap-3 rounded-[10px] border border-line bg-panel px-4 py-3.5">
                {data.cards.length > 0 && (
                  <ul className="divide-y divide-line">
                    {data.cards.map((c) => (
                      <li key={c.id} className="flex items-center gap-3 py-2 text-[13px]">
                        <span className="flex-1">{c.label} <span className="text-fg-dim">· {String(c.exp_month).padStart(2, '0')}/{String(c.exp_year).slice(-2)}{c.is_default ? tr('ui.payments.default') : ''}</span></span>
                        <button type="button" onClick={() => void removeCard(c)} className="min-h-9 px-2 text-danger-ink hover:underline">{tr('ui.payments.remove')}</button>
                      </li>
                    ))}
                  </ul>
                )}
                {data.settings.enabled && <AddCard onAdded={() => refetch({ silent: true })} />}
              </div>
            </section>
            )}
            {(data.settings.enabled || history.length > 0) && (
            <section className="flex flex-col gap-2">
              <h2 className="font-mono text-[11px] tracking-wide text-fg-dim">{tr('ui.payments.history')}</h2>
              {history.length === 0 ? (
                <p className="text-[13px] text-fg-dim">{tr('ui.payments.noPaymentsYet')}</p>
              ) : (
                <ul className="divide-y divide-line rounded-[10px] border border-line bg-panel">
                  {history.map((r) => (
                    <li key={r.id} className={cn('flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-2.5 text-[13px]', r.id === focus && 'bg-panel-2')}>
                      <span className="tabular-nums font-medium">{money(r.amount_cents, r.currency)}</span>
                      <span className="min-w-0 flex-1 truncate">{r.merchant} <span className="text-fg-dim">· {r.agent_name || tr('ui.payments.agent')} · {when(r.created_at)}</span></span>
                      <span className={cn('font-mono text-[11px]', r.status === 'paid' ? 'text-accent-ink' : r.status === 'unconfirmed' ? 'text-danger-ink' : 'text-fg-dim')}>{STATUS[r.status] ?? r.status}</span>
                      {r.result && <p className="basis-full text-[12px] text-fg-dim">{r.result}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
