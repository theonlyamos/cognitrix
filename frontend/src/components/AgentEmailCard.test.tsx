import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AgentEmailCard } from './AgentEmailCard';
import type { AgentEmailState, EmailAccount } from '@/lib/agentEmail';

const email = vi.hoisted(() => ({
  getAgentEmail: vi.fn(),
  saveAgentEmail: vi.fn(),
  saveAgentAddress: vi.fn(),
  testAgentEmail: vi.fn(),
  deleteAgentEmail: vi.fn(),
}));

vi.mock('@/lib/agentEmail', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/agentEmail')>()),
  ...email,
}));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: vi.fn().mockResolvedValue({ data: [{ id: 'a1', name: 'Researcher' }] }) },
}));

const account: EmailAccount = {
  address: 'agents@example.com', addressing: 'plus', imap_host: 'imap.gmail.com', imap_port: 993, imap_security: 'ssl',
  smtp_host: 'smtp.gmail.com', smtp_port: 465, smtp_security: 'ssl', username: '', password_set: true, folder: 'INBOX',
  agent_id: '', allowed_senders: '', require_auth: true, answer_others: false, enabled: true, status: 'connected',
  last_error: '', last_checked_at: '', running: true,
};

const connected: AgentEmailState = {
  available: true, owner_email: 'me@example.com', account,
  agents: [{ agent_id: 'a1', agent_name: 'Researcher', name: 'researcher', enabled: true, address: 'agents+researcher@example.com' }],
};

beforeEach(() => {
  Object.values(email).forEach((fn) => fn.mockReset());
});

describe('AgentEmailCard', () => {
  it('connects a Gmail mailbox with the preset servers', async () => {
    email.getAgentEmail.mockResolvedValue({ available: true, owner_email: 'me@example.com', account: null, agents: [] });
    email.saveAgentEmail.mockResolvedValue(connected);
    render(<AgentEmailCard />);
    fireEvent.change(await screen.findByLabelText('Mailbox address'), { target: { value: 'agents@example.com' } });
    fireEvent.change(screen.getByLabelText('Mailbox password'), { target: { value: 'app pass' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect mailbox' }));

    await waitFor(() => expect(email.saveAgentEmail).toHaveBeenCalledWith(expect.objectContaining({
      address: 'agents@example.com', password: 'app pass', imap_host: 'imap.gmail.com', smtp_host: 'smtp.gmail.com', addressing: 'plus',
    })));
    expect(await screen.findByLabelText('Researcher email address')).toHaveTextContent('agents+researcher@example.com');
  });

  it('turns an agent address off and saves allowed senders', async () => {
    email.getAgentEmail.mockResolvedValue(connected);
    email.saveAgentAddress.mockResolvedValue(connected);
    email.saveAgentEmail.mockResolvedValue(connected);
    render(<AgentEmailCard />);
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Researcher email on' }));
    await waitFor(() => expect(email.saveAgentAddress).toHaveBeenCalledWith('a1', { enabled: false }));

    fireEvent.change(screen.getByLabelText('Allowed senders'), { target: { value: '@corp.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save senders' }));
    await waitFor(() => expect(email.saveAgentEmail).toHaveBeenCalledWith({ allowed_senders: '@corp.com' }));
  });
});
