import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { MobileHeaderActions } from '@/components/mobile-header-actions';

function ActionsHarness({ onDelete = vi.fn() }: { onDelete?: () => void }) {
  const [deleted, setDeleted] = useState(false);

  return (
    <>
      <MobileHeaderActions
        id="agent-actions"
        actions={[
          { key: 'edit', label: 'Edit agent', to: '/agents/agent-1/edit' },
          {
            key: 'delete',
            label: 'Delete agent',
            destructive: true,
            onSelect: () => {
              onDelete();
              setDeleted(true);
            },
          },
        ]}
      />
      {deleted && <p>Deleted</p>}
    </>
  );
}

describe('MobileHeaderActions', () => {
  it('opens contextual actions in the existing accessible mobile sheet', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}><ActionsHarness /></MemoryRouter>);

    const trigger = screen.getByRole('button', { name: 'More actions' });
    expect(trigger).toHaveClass('h-11', 'w-11', 'md:hidden');
    expect(trigger).toHaveAttribute('aria-controls', 'agent-actions');

    await user.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'Page actions' });
    expect(within(dialog).getByRole('link', { name: 'Edit agent' })).toHaveClass('min-h-11', 'w-full');
    expect(within(dialog).getByRole('button', { name: 'Delete agent' })).toHaveClass('min-h-11', 'w-full');
  });

  it('closes on Escape and restores focus to More actions', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}><ActionsHarness /></MemoryRouter>);

    const trigger = screen.getByRole('button', { name: 'More actions' });
    await user.click(trigger);
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'Page actions' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('runs a callback action and closes the sheet', async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(<MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}><ActionsHarness onDelete={onDelete} /></MemoryRouter>);

    const trigger = screen.getByRole('button', { name: 'More actions' });
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'Delete agent' }));

    expect(onDelete).toHaveBeenCalledOnce();
    expect(screen.getByText('Deleted')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'Page actions' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
