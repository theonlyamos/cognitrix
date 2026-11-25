import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { QuoteChips, SelectionReply } from '@/components/SelectionReply';

const flush = () => act(async () => { await new Promise((r) => requestAnimationFrame(() => r(null))); });

function select(node: Node) {
  const range = document.createRange();
  range.selectNodeContents(node);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

describe('SelectionReply', () => {
  it('offers Reply for text picked in an agent reply and hands it over', async () => {
    const onReply = vi.fn();
    render(
      <div>
        <div data-reply-source=""><p data-testid="reply">Use port 8080 for the server.</p></div>
        <SelectionReply onReply={onReply} />
      </div>,
    );
    select(screen.getByTestId('reply'));
    fireEvent.pointerUp(document);
    await flush();
    const button = screen.getByRole('button', { name: 'Reply' });
    fireEvent.click(button);
    expect(onReply).toHaveBeenCalledWith('Use port 8080 for the server.');
    expect(screen.queryByRole('button', { name: 'Reply' })).toBeNull();
  });

  it('stays hidden for text outside agent replies', async () => {
    render(
      <div>
        <p data-testid="mine">My own message</p>
        <SelectionReply onReply={() => {}} />
      </div>,
    );
    select(screen.getByTestId('mine'));
    fireEvent.pointerUp(document);
    await flush();
    expect(screen.queryByRole('button', { name: 'Reply' })).toBeNull();
  });
});

describe('QuoteChips', () => {
  it('lists quotes and removes one', () => {
    const onRemove = vi.fn();
    render(<QuoteChips quotes={['first bit', 'second bit']} onRemove={onRemove} />);
    expect(screen.getByText('first bit')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove quote' })[1]);
    expect(onRemove).toHaveBeenCalledWith(1);
  });
});
