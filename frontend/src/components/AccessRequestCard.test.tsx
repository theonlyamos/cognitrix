import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessRequestCard, type AccessRequest } from './AccessRequestCard';

const signIn = vi.fn();
vi.mock('@/lib/apps', () => ({
  signInWithBrowser: (start: () => unknown) => signIn(start),
  startAppSignIn: vi.fn(),
}));

const tool: AccessRequest = {
  kind: 'tool', name: 'Bash', label: 'Bash', reason: 'Run the test suite', description: 'Run a shell command.', can_add: true,
};
const app = (connected: boolean, extra: Partial<AccessRequest['app']> = {}): AccessRequest => ({
  kind: 'app', name: 'gmail', label: 'Gmail', reason: 'Find the invoice', can_add: true,
  app: { id: 'gmail', connected, can_connect: true, setup: '', ...extra },
});

describe('AccessRequestCard', () => {
  beforeEach(() => signIn.mockReset());

  it('allows a tool in this chat, adds it to the agent, or denies it', async () => {
    const onAnswer = vi.fn();
    render(<AccessRequestCard access={tool} agentName="Ada" onAnswer={onAnswer} />);
    expect(screen.getByText('Run the test suite')).toBeInTheDocument();
    expect(screen.getByText(/Ada wants to use/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Allow in this chat' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add to Ada' }));
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }));
    expect(onAnswer.mock.calls).toEqual([['chat'], ['agent'], [null]]);
  });

  it('only offers "Add to agent" to its owner', () => {
    render(<AccessRequestCard access={{ ...tool, can_add: false }} onAnswer={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Add to/ })).not.toBeInTheDocument();
  });

  it('connects an app first, then allows it', async () => {
    signIn.mockResolvedValue({ status: 'done', error: null });
    const onAnswer = vi.fn();
    render(<AccessRequestCard access={app(false)} onAnswer={onAnswer} />);
    expect(screen.queryByRole('button', { name: 'Allow in this chat' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add to/ })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Connect Gmail' }));
    expect(signIn).toHaveBeenCalledTimes(1);
    await userEvent.click(await screen.findByRole('button', { name: 'Allow in this chat' }));
    expect(onAnswer).toHaveBeenCalledWith('chat');
  });

  it('shows a failed sign-in and keeps waiting', async () => {
    signIn.mockResolvedValue({ status: 'error', error: 'Access was refused' });
    render(<AccessRequestCard access={app(false)} onAnswer={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Connect Gmail' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Access was refused');
    expect(screen.getByRole('button', { name: 'Connect Gmail' })).toBeEnabled();
  });

  it('a connected app can be allowed straight away', async () => {
    const onAnswer = vi.fn();
    render(<AccessRequestCard access={app(true)} onAnswer={onAnswer} />);
    expect(screen.getByText('Connected')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Add to agent' }));
    expect(onAnswer).toHaveBeenCalledWith('agent');
  });
});
