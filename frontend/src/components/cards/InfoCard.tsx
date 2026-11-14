import { lazy, Suspense, useState, type ReactNode } from 'react';
import { CardChart } from '@/components/cards/CardChart';
import { buttonMessage, requestCardAction, type Card, type CardBlock, type CardButton } from '@/lib/cards';
import { cn } from '@/lib/utils';

const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));

const PRESSED_KEY = 'cognitrix:card-pressed';

function readPressed(id: string): string | null {
  try {
    return (JSON.parse(localStorage.getItem(PRESSED_KEY) || '{}') as Record<string, string>)[id] ?? null;
  } catch {
    return null;
  }
}

function savePressed(id: string, label: string) {
  try {
    const all = JSON.parse(localStorage.getItem(PRESSED_KEY) || '{}') as Record<string, string>;
    all[id] = label;
    // Keep the newest 200 cards.
    const keys = Object.keys(all);
    for (const old of keys.slice(0, Math.max(0, keys.length - 200))) delete all[old];
    localStorage.setItem(PRESSED_KEY, JSON.stringify(all));
  } catch {
    // Storage off: the press still went to the agent.
  }
}

const Label = ({ children }: { children: ReactNode }) => (
  <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-fg-dim">{children}</div>
);

function Fields({ items }: { items: Extract<CardBlock, { type: 'fields' }>['items'] }) {
  return (
    <div className="flex flex-wrap gap-x-7 gap-y-3">
      {items.map((f, i) => (
        <div key={i} className="min-w-0">
          <Label>{f.label}</Label>
          <div className="mt-0.5 text-[21px] font-semibold leading-tight tracking-[-0.01em] tabular-nums">{f.value}</div>
          {f.change && (
            <div className={cn('font-mono text-[11px]', f.tone === 'good' ? 'text-ok' : f.tone === 'bad' ? 'text-danger-ink' : 'text-fg-dim')}>
              {f.change}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Table({ table }: { table: Extract<CardBlock, { type: 'table' }> }) {
  return (
    <div className="min-w-0">
      {table.title && <Label>{table.title}</Label>}
      <div className="mt-1.5 overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              {table.columns.map((c, i) => (
                <th key={i} className={cn('border-b border-line px-2 py-1.5 font-mono text-[10px] font-normal uppercase tracking-[0.1em] text-fg-dim', table.numeric?.[i] ? 'text-right' : 'text-left')}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, r) => (
              <tr key={r} className="border-b border-line last:border-b-0">
                {row.map((cell, i) => (
                  <td key={i} className={cn('px-2 py-1.5', table.numeric?.[i] && 'text-right font-mono text-[12px] tabular-nums')}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Block({ block }: { block: CardBlock }) {
  switch (block.type) {
    case 'text':
      return (
        <div className="md break-words text-[14px]">
          <Suspense fallback={<div className="whitespace-pre-wrap">{block.text}</div>}><MarkdownMessage content={block.text} /></Suspense>
        </div>
      );
    case 'fields':
      return <Fields items={block.items} />;
    case 'table':
      return <Table table={block} />;
    case 'chart':
      return (
        <div className="min-w-0">
          {(block.title || block.unit) && <Label>{[block.title, block.unit].filter(Boolean).join(' · ')}</Label>}
          <div className="mt-1.5"><CardChart chart={block} /></div>
        </div>
      );
    case 'image':
      return <img src={block.url} alt={block.alt || ''} className="max-h-80 max-w-full rounded border border-line" loading="lazy" referrerPolicy="no-referrer" />;
    case 'divider':
      return <hr className="border-line" />;
    case 'section':
      return (
        <div className="space-y-3">
          {block.title && <div className="text-[14px] font-semibold">{block.title}</div>}
          <Blocks blocks={block.blocks} />
        </div>
      );
    default:
      return null;
  }
}

/** Blocks in order; a chart next to a table share a row on wide screens. */
function Blocks({ blocks }: { blocks: CardBlock[] }) {
  const out: ReactNode[] = [];
  for (let i = 0; i < blocks.length; i += 1) {
    const b = blocks[i];
    if (b.type === 'actions') continue;
    const next = blocks[i + 1];
    if (next && ((b.type === 'chart' && next.type === 'table') || (b.type === 'table' && next.type === 'chart'))) {
      out.push(
        <div key={i} className="grid gap-5 md:grid-cols-2">
          <Block block={b} />
          <Block block={next} />
        </div>,
      );
      i += 1;
    } else {
      out.push(<Block key={i} block={b} />);
    }
  }
  return <>{out}</>;
}

const collectButtons = (blocks: CardBlock[]): CardButton[] => blocks.flatMap((b) => (
  b.type === 'actions' ? b.buttons : b.type === 'section' ? collectButtons(b.blocks) : []
));

/** Style C: the card reads as part of the reply, marked by a rule on its
 *  left; its buttons sit under it as "next steps". A press is sent to the
 *  agent as a chat message. */
export function InfoCard({ card }: { card: Card }) {
  const [pressed, setPressed] = useState<string | null>(() => readPressed(card.id));
  const [note, setNote] = useState<string | null>(null);
  const buttons = collectButtons(card.blocks);

  const press = (button: CardButton) => {
    if (pressed) return;
    const sent = requestCardAction(buttonMessage(card, button));
    if (!sent) {
      setNote('Buttons work in the chat this card came from.');
      return;
    }
    // Hold the buttons while the press is sent; give them back if it fails.
    setPressed(button.label);
    setNote(null);
    void sent.then((ok) => {
      if (ok) {
        savePressed(card.id, button.label);
      } else {
        setPressed(null);
        setNote("Couldn't send that. Check the connection and try again.");
      }
    });
  };

  return (
    <div className="w-full max-w-3xl space-y-3" data-testid="info-card">
      <div className="space-y-4 border-l-[3px] border-accent py-0.5 pl-4">
        {(card.title || card.subtitle) && (
          <div>
            {card.title && <div className="text-[16.5px] font-semibold leading-snug">{card.title}</div>}
            {card.subtitle && <div className="mt-0.5 text-[12.5px] text-fg-dim">{card.subtitle}</div>}
          </div>
        )}
        <Blocks blocks={card.blocks} />
      </div>
      {buttons.length > 0 && (
        <div className="space-y-1.5">
          <Label>Next steps</Label>
          <div className="flex flex-wrap gap-2">
            {buttons.map((b, i) => (b.url ? (
              <a key={i} href={b.url} target="_blank" rel="noreferrer nofollow"
                className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-line bg-panel px-3 py-1 text-[13px] transition-colors hover:border-fg-dim md:min-h-0">
                {b.label}<span aria-hidden className="text-fg-dim">↗</span>
              </a>
            ) : (
              <button key={i} type="button" onClick={() => press(b)} disabled={!!pressed}
                aria-pressed={pressed === b.label}
                className={cn(
                  'inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] transition-colors md:min-h-0',
                  pressed === b.label
                    ? 'border-transparent bg-accent text-accent-foreground'
                    : pressed
                      ? 'border-line bg-panel opacity-45'
                      : b.style === 'danger'
                        ? 'border-line bg-panel hover:border-danger hover:text-danger-ink'
                        : b.style === 'primary'
                          ? 'border-accent-ink bg-panel text-accent-ink hover:bg-accent/10'
                          : 'border-line bg-panel hover:border-fg-dim',
                )}>
                {pressed === b.label && <span aria-hidden>✓</span>}
                {b.label}
              </button>
            )))}
          </div>
          {note && <div className="font-mono text-[11px] text-fg-dim">{note}</div>}
        </div>
      )}
    </div>
  );
}

/** While the agent is still writing the card. */
export function InfoCardPending() {
  return (
    <div className="w-full max-w-3xl space-y-3 border-l-[3px] border-accent/50 py-0.5 pl-4" aria-label="Writing card">
      <div className="h-4 w-48 animate-pulse rounded bg-panel-2" />
      <div className="flex gap-6">
        {[0, 1, 2].map((i) => <div key={i} className="h-10 w-20 animate-pulse rounded bg-panel-2" />)}
      </div>
      <div className="h-24 w-full animate-pulse rounded bg-panel-2" />
    </div>
  );
}
