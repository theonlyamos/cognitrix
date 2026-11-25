/**
 * Select text in an agent's reply and press Reply: the selection goes into
 * the next message as a quote, so the agent knows which part is meant.
 *
 * A quote travels as a Markdown blockquote at the top of the message
 * ("> picked text", a blank line, then what the person typed). Models read
 * that as "about this part"; the chat shows it as a quote above the words.
 */

/** Longest quote kept, so a select-all doesn't paste a whole reply back. */
export const MAX_QUOTE_CHARS = 2000;

/** Tidy picked text: trim, collapse runs of blank lines, cap the length. */
export function cleanQuote(text: string): string {
  const tidy = text.replace(/\r\n?/g, '\n').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  return tidy.length > MAX_QUOTE_CHARS ? `${tidy.slice(0, MAX_QUOTE_CHARS).trimEnd()}…` : tidy;
}

/** The message as sent: each quote as a blockquote, then the person's words. */
export function withQuotes(text: string, quotes: string[]): string {
  const blocks = quotes.map(cleanQuote).filter(Boolean)
    .map((q) => q.split('\n').map((line) => (line ? `> ${line}` : '>')).join('\n'));
  if (!blocks.length) return text;
  return [...blocks, text].filter(Boolean).join('\n\n');
}

/** Split a sent message back into its leading quotes and the rest. */
export function splitQuotes(content: string): { quotes: string[]; text: string } {
  const quotes: string[] = [];
  const paragraphs = content.split(/\n[ \t]*\n/);
  let i = 0;
  for (; i < paragraphs.length; i += 1) {
    const lines = paragraphs[i].split('\n');
    if (!lines.every((line) => /^>( |$)/.test(line))) break;
    quotes.push(lines.map((line) => line.replace(/^> ?/, '')).join('\n'));
  }
  if (!quotes.length) return { quotes, text: content };
  return { quotes, text: paragraphs.slice(i).join('\n\n') };
}

/** The reply text the selection sits in, or null when it isn't one. */
export function replySourceOf(node: Node | null): HTMLElement | null {
  const el = node instanceof HTMLElement ? node : node?.parentElement ?? null;
  return el?.closest<HTMLElement>('[data-reply-source]') ?? null;
}

/** A one-line label for a sent message: its words, or the quote when it has none. */
export function askLabel(content: string): string {
  const { quotes, text } = splitQuotes(content);
  if (text.trim()) return text;
  return quotes.length ? `“${quotes[0]}”` : ''; // i18n-ignore
}
