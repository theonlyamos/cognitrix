// Agent cards: small layouts an agent shows with the Show Card tool.
// The server checks the card (cognitrix/cards/spec.py) and sends its clean
// shape on the tool outcome; this file only re-checks the shape it relies on.

export type CardTone = 'neutral' | 'good' | 'bad';
export interface CardField { label: string; value: string; change?: string; tone?: CardTone }
export interface CardButton { label: string; action?: string; value?: string; style?: 'default' | 'primary' | 'danger'; url?: string }
export interface CardSeries { name: string; values: number[] }

export type CardBlock =
  | { type: 'text'; text: string }
  | { type: 'fields'; items: CardField[] }
  | { type: 'table'; title?: string; columns: string[]; rows: string[][]; numeric?: boolean[] }
  | { type: 'chart'; kind: 'bar' | 'line' | 'area' | 'pie'; title?: string; unit?: string; labels: string[]; series: CardSeries[] }
  | { type: 'image'; url: string; alt?: string }
  | { type: 'divider' }
  | { type: 'actions'; buttons: CardButton[] }
  | { type: 'section'; title?: string; blocks: CardBlock[] };

export interface Card { id: string; title: string; subtitle?: string; blocks: CardBlock[] }

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.map(str) : []);

function block(raw: unknown, depth = 0): CardBlock | null {
  if (!isObj(raw)) return null;
  switch (raw.type) {
    case 'text':
      return str(raw.text) ? { type: 'text', text: str(raw.text) } : null;
    case 'fields': {
      const items = (Array.isArray(raw.items) ? raw.items : []).filter(isObj).map((f) => ({
        label: str(f.label),
        value: str(f.value),
        ...(str(f.change) ? { change: str(f.change) } : {}),
        tone: (['good', 'bad'].includes(str(f.tone)) ? f.tone : 'neutral') as CardTone,
      })).filter((f) => f.label);
      return items.length ? { type: 'fields', items } : null;
    }
    case 'table': {
      const columns = strList(raw.columns);
      const rows = (Array.isArray(raw.rows) ? raw.rows : []).map(strList).filter((r) => r.length === columns.length);
      const numeric = Array.isArray(raw.numeric) ? raw.numeric.map(Boolean) : undefined;
      return columns.length ? { type: 'table', columns, rows, ...(numeric ? { numeric } : {}), ...(str(raw.title) ? { title: str(raw.title) } : {}) } : null;
    }
    case 'chart': {
      const kind = str(raw.kind);
      if (!['bar', 'line', 'area', 'pie'].includes(kind)) return null;
      const labels = strList(raw.labels);
      const series = (Array.isArray(raw.series) ? raw.series : []).filter(isObj).map((s) => ({
        name: str(s.name),
        values: (Array.isArray(s.values) ? s.values : []).map((n) => (typeof n === 'number' && Number.isFinite(n) ? n : 0)),
      })).filter((s) => s.values.length === labels.length);
      if (!labels.length || !series.length) return null;
      return {
        type: 'chart', kind: kind as 'bar', labels, series,
        ...(str(raw.title) ? { title: str(raw.title) } : {}),
        ...(str(raw.unit) ? { unit: str(raw.unit) } : {}),
      };
    }
    case 'image': {
      const url = str(raw.url);
      return /^(https:\/\/|data:image\/(png|jpeg|gif|webp);base64,)/.test(url) ? { type: 'image', url, alt: str(raw.alt) } : null;
    }
    case 'divider':
      return { type: 'divider' };
    case 'actions': {
      const buttons = (Array.isArray(raw.buttons) ? raw.buttons : []).filter(isObj).map((b): CardButton | null => {
        const label = str(b.label);
        if (!label) return null;
        const url = str(b.url);
        if (url) return /^(https?:\/\/|\/)/.test(url) ? { label, url } : null;
        const style = str(b.style);
        return {
          label,
          action: str(b.action) || 'button',
          ...(str(b.value) ? { value: str(b.value) } : {}),
          style: (['primary', 'danger'].includes(style) ? style : 'default') as CardButton['style'],
        };
      }).filter((b): b is CardButton => b !== null);
      return buttons.length ? { type: 'actions', buttons } : null;
    }
    case 'section': {
      if (depth > 0) return null;
      const blocks = (Array.isArray(raw.blocks) ? raw.blocks : []).map((b) => block(b, depth + 1)).filter((b): b is CardBlock => b !== null);
      return blocks.length || str(raw.title) ? { type: 'section', blocks, ...(str(raw.title) ? { title: str(raw.title) } : {}) } : null;
    }
    default:
      return null;
  }
}

/** A card from a tool outcome, or null when it isn't one. */
export function parseCard(value: unknown): Card | null {
  if (!isObj(value) || !Array.isArray(value.blocks)) return null;
  const blocks = value.blocks.map((b) => block(b)).filter((b): b is CardBlock => b !== null);
  const title = str(value.title);
  if (!title && !blocks.length) return null;
  return { id: str(value.id) || 'card', title, ...(str(value.subtitle) ? { subtitle: str(value.subtitle) } : {}), blocks };
}

/** What the agent is told when a button is pressed (matches the server's
 *  cognitrix.cards.button_message). */
export function buttonMessage(card: Card, button: CardButton): string {
  const extra = button.value ? `, value: ${button.value}` : '';
  return `[Pressed "${button.label}" on card "${card.title || 'card'}" (action: ${button.action}${extra})]`;
}

const PRESS_RE = /^\[Pressed "(.{1,60}?)" on card "(.{0,200}?)" \(action: ([A-Za-z0-9_.:-]{1,64})(?:, value: .*)?\)\]$/;

/** A button-press message, shown in the chat as a small line, or null. */
export function parsePress(text: string): { label: string; card: string; action: string } | null {
  const match = PRESS_RE.exec(text.trim());
  return match ? { label: match[1], card: match[2], action: match[3] } : null;
}

const ACTION_EVENT = 'cognitrix:card-action';

/** Send a button press to the chat on screen. Returns false when no chat took it. */
/** Ask the open chat to send a card press. Returns null when no chat is
 *  listening, otherwise whether the message was sent. */
export function requestCardAction(text: string): Promise<boolean> | null {
  const detail: { text: string; result?: Promise<boolean> } = { text };
  const event = new CustomEvent(ACTION_EVENT, { detail, cancelable: true });
  if (window.dispatchEvent(event)) return null;
  return detail.result ?? Promise.resolve(true);
}

export function onCardAction(handler: (text: string) => boolean | Promise<boolean>): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<{ text?: string; result?: Promise<boolean> }>).detail;
    if (!detail?.text) return;
    event.preventDefault();
    detail.result = Promise.resolve(handler(detail.text)).then((ok) => ok !== false, () => false);
  };
  window.addEventListener(ACTION_EVENT, listener);
  return () => window.removeEventListener(ACTION_EVENT, listener);
}
