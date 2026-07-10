import { useRef, useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MobileSheet } from '@/components/MobileSheet';

function SheetHarness() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const destinationRef = useRef<HTMLButtonElement>(null);

  return (
    <>
      <button ref={triggerRef} onClick={() => setOpen(true)}>Open history</button>
      <MobileSheet
        id="history-sheet"
        label="History"
        open={open}
        onClose={() => setOpen(false)}
        triggerRef={triggerRef}
      >
        <button>First action</button>
        <button
          onClick={() => {
            destinationRef.current?.focus();
            setOpen(false);
          }}
        >
          Open destination
        </button>
        <button onClick={() => setOpen(false)}>Last action</button>
      </MobileSheet>
      <button ref={destinationRef}>Outside destination</button>
    </>
  );
}

describe('MobileSheet', () => {
  it('opens as a modal dialog and focuses its first action', async () => {
    render(<SheetHarness />);

    await userEvent.click(screen.getByRole('button', { name: 'Open history' }));

    const dialog = screen.getByRole('dialog', { name: 'History' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(within(dialog).getByRole('button', { name: 'First action' })).toHaveFocus();
    expect(screen.getByTestId('mobile-sheet-backdrop')).toHaveAttribute('tabindex', '-1');
    expect(screen.getByTestId('mobile-sheet-backdrop')).toHaveAttribute('aria-hidden', 'true');
  });

  it('contains forward and backward Tab navigation', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open history' }));

    const first = screen.getByRole('button', { name: 'First action' });
    const last = screen.getByRole('button', { name: 'Last action' });

    await userEvent.tab({ shift: true });
    expect(last).toHaveFocus();
    await userEvent.tab();
    expect(first).toHaveFocus();
  });

  it('closes on Escape and restores focus to its trigger', async () => {
    render(<SheetHarness />);
    const trigger = screen.getByRole('button', { name: 'Open history' });
    await userEvent.click(trigger);

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'History' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('closes from an in-sheet action and restores trigger focus', async () => {
    render(<SheetHarness />);
    const trigger = screen.getByRole('button', { name: 'Open history' });
    await userEvent.click(trigger);

    await userEvent.click(screen.getByRole('button', { name: 'Last action' }));

    expect(screen.queryByRole('dialog', { name: 'History' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('preserves an explicit focus handoff to an outside destination', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open history' }));

    await userEvent.click(screen.getByRole('button', { name: 'Open destination' }));

    expect(screen.queryByRole('dialog', { name: 'History' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Outside destination' })).toHaveFocus();
  });
});
