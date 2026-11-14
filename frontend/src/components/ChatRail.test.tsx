import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatRail, type RailGroup } from './ChatRail';

const groups: RailGroup[] = [
  { key: 'agent:a1', kind: 'agent', id: 'a1', name: 'Assistant', current: true, chats: [
    { id: 'c1', title: 'Plan a trip to Lisbon', when: '2m ago', running: true, selected: true },
    { id: 'c2', title: 'Market today', when: '1d ago' },
  ] },
  { key: 'team:t1', kind: 'team', id: 't1', name: 'Launch Team', chats: [{ id: 'c3', title: 'Launch plan', when: '1h ago' }] },
];

function setup(overrides: Partial<Parameters<typeof ChatRail>[0]> = {}) {
  const props = {
    groups,
    pinned: [{ id: 'c2', title: 'Market today', when: '1d ago' }],
    archivedCount: 2,
    onExpand: vi.fn(),
    onNew: vi.fn(),
    onNewWith: vi.fn(),
    onOpenChat: vi.fn(),
    ...overrides,
  };
  render(<ChatRail {...props} />);
  return props;
}

describe('ChatRail', () => {
  it('shows one thumbnail per agent and team, with chat counts in their names', () => {
    setup();
    expect(screen.getByRole('button', { name: 'Assistant, 2 chats' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Launch Team team, 1 chat' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pinned, 1 chat' })).toBeInTheDocument();
    expect(screen.getByLabelText('working')).toBeInTheDocument();
  });

  it('opens a flyout with that agent\'s chats and opens the picked chat', async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole('button', { name: 'Assistant, 2 chats' }));
    const flyout = screen.getByRole('dialog', { name: 'Assistant chats' });
    expect(within(flyout).getByRole('button', { name: /Plan a trip to Lisbon/ })).toHaveAttribute('aria-current', 'true');
    await user.click(within(flyout).getByRole('button', { name: /Market today/ }));
    expect(props.onOpenChat).toHaveBeenCalledWith('c2');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('starts a chat with the agent from the flyout and closes on Escape', async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole('button', { name: 'Launch Team team, 1 chat' }));
    await user.click(screen.getByRole('button', { name: 'New chat with Launch Team' }));
    expect(props.onNewWith).toHaveBeenCalledWith(expect.objectContaining({ key: 'team:t1' }));

    await user.click(screen.getByRole('button', { name: 'Assistant, 2 chats' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Assistant, 2 chats' })).toHaveFocus();
  });

  it('goes back to the full list, at a section when asked', async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole('button', { name: 'Show the full chat list' }));
    expect(props.onExpand).toHaveBeenCalledWith();
    await user.click(screen.getByRole('button', { name: 'Archived, 2 chats' }));
    expect(props.onExpand).toHaveBeenCalledWith('archived');
    await user.click(screen.getByRole('button', { name: 'Assistant, 2 chats' }));
    await user.click(screen.getByRole('button', { name: /open in full list/ }));
    expect(props.onExpand).toHaveBeenCalledWith('agent:a1');
  });
});
