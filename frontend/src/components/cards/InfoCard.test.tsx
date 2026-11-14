import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InfoCard } from '@/components/cards/InfoCard';
import { PanelButton, PanelCard } from '@/components/cards/PanelCard';
import { buttonMessage, onCardAction, parseCard, parsePress } from '@/lib/cards';
import { parseChatEntries, toChatMessages } from '@/lib/transcript';

const RAW = {
  id: 'card_abc',
  title: 'Weekly sales',
  subtitle: 'Nov 7-13',
  blocks: [
    { type: 'fields', items: [{ label: 'Revenue', value: '$48,210', change: '+12%', tone: 'good' }, { label: 'Refunds', value: '1.8%', change: '+0.6 pt', tone: 'bad' }] },
    { type: 'chart', kind: 'bar', title: 'Revenue by day', unit: '$k', labels: ['Mon', 'Tue'], series: [{ name: 'Revenue', values: [5.1, 6.2] }] },
    { type: 'table', title: 'Top products', columns: ['Product', 'Units'], rows: [['Linen shirt', '212']], numeric: [false, true] },
    { type: 'actions', buttons: [{ label: 'Email to team', action: 'email_team', style: 'primary' }, { label: 'Open report', url: 'https://example.com/r' }] },
    { type: 'script', code: 'alert(1)' },
  ],
};

afterEach(() => localStorage.clear());

describe('agent cards', () => {
  it('keeps only the shapes it knows', () => {
    const card = parseCard(RAW)!;
    expect(card.blocks.map((b) => b.type)).toEqual(['fields', 'chart', 'table', 'actions']);
    expect(parseCard({ blocks: [{ type: 'actions', buttons: [{ label: 'x', url: 'javascript:alert(1)' }] }] })).toBeNull();
    expect(parseCard('nope')).toBeNull();
  });

  it('draws the card and sends a press to the chat', async () => {
    const card = parseCard(RAW)!;
    const got = vi.fn();
    const off = onCardAction(got);
    render(<InfoCard card={card} />);
    expect(screen.getByText('Weekly sales')).toBeInTheDocument();
    expect(screen.getByText('$48,210')).toBeInTheDocument();
    expect(screen.getByText('+0.6 pt')).toHaveClass('text-danger-ink');
    expect(screen.getByRole('img', { name: 'Revenue by day' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open report/ })).toHaveAttribute('href', 'https://example.com/r');

    await userEvent.click(screen.getByRole('button', { name: 'Email to team' }));
    expect(got).toHaveBeenCalledWith('[Pressed "Email to team" on card "Weekly sales" (action: email_team)]');
    expect(screen.getByRole('button', { name: /Email to team/ })).toHaveAttribute('aria-pressed', 'true');
    off();
  });

  it('says so when no chat can take the press', async () => {
    render(<InfoCard card={{ ...parseCard(RAW)!, id: 'card_other' }} />);
    await userEvent.click(screen.getByRole('button', { name: 'Email to team' }));
    expect(screen.getByText('Buttons work in the chat this card came from.')).toBeInTheDocument();
  });

  it('reads a press message back', () => {
    const card = parseCard(RAW)!;
    const text = buttonMessage(card, { label: 'Look into refunds', action: 'refunds', value: 'linen' });
    expect(parsePress(text)).toEqual({ label: 'Look into refunds', card: 'Weekly sales', action: 'refunds' });
    expect(parsePress('hello')).toBeNull();
  });

  it('restores the card from a saved chat', () => {
    const messages = toChatMessages(parseChatEntries([
      { role: 'assistant', type: 'tool_calls', content: '', tool_calls: [{ name: 'Show Card', arguments: {}, tool_call_id: 't1' }] },
      { role: 'tool', tool_call_id: 't1', content: 'Showed', outcome: { status: 'success', card: RAW } },
    ]));
    expect(messages[0].tools?.[0].card?.title).toBe('Weekly sales');
  });

  it('panel cards hold facts, a body and actions', async () => {
    const approve = vi.fn();
    render(
      <PanelCard tag="approval" title="Run Bash?" fields={[{ label: 'Risk', value: 'high' }]} footer={<PanelButton kind="primary" onClick={approve}>approve</PanelButton>}>
        <pre>rm -rf build</pre>
      </PanelCard>,
    );
    const panel = screen.getByRole('heading', { name: 'Run Bash?' }).closest('section')!;
    expect(within(panel).getByText('high')).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole('button', { name: 'approve' }));
    expect(approve).toHaveBeenCalled();
  });
});
