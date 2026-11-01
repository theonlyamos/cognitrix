import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CredentialsCard } from './CredentialsCard';
import type { Credential } from '@/lib/credentials';

const lib = vi.hoisted(() => ({
  listCredentials: vi.fn(),
  addCredential: vi.fn(),
  updateCredential: vi.fn(),
  deleteCredential: vi.fn(),
}));

vi.mock('@/lib/credentials', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/credentials')>()),
  ...lib,
}));

const github: Credential = {
  id: 'c1', name: 'GITHUB_TOKEN', kind: 'token', description: 'GitHub API', allowed_hosts: ['api.github.com'],
  header: '', hint: '…e123', username: '', last_used_at: null, placeholder: '{{secret:GITHUB_TOKEN}}',
};

beforeEach(() => {
  Object.values(lib).forEach((fn) => fn.mockReset());
  lib.listCredentials.mockResolvedValue([github]);
});

describe('CredentialsCard', () => {
  it('lists saved sign-ins with their hosts but no values', async () => {
    render(<CredentialsCard />);
    expect(await screen.findByText('GITHUB_TOKEN')).toBeInTheDocument();
    expect(screen.getByText('…e123')).toBeInTheDocument();
    expect(screen.getByText(/Only sent to api.github.com/)).toBeInTheDocument();
  });

  it('adds a token with its hosts', async () => {
    lib.addCredential.mockResolvedValue(github);
    render(<CredentialsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add sign-in' }));
    fireEvent.change(screen.getByPlaceholderText('GITHUB_TOKEN'), { target: { value: 'stripe' } });
    fireEvent.change(screen.getByPlaceholderText('api.github.com'), { target: { value: 'api.stripe.com, files.stripe.com' } });
    fireEvent.change(screen.getByPlaceholderText('ghp_…'), { target: { value: 'sk_test_123' } });
    fireEvent.change(screen.getByPlaceholderText('X-API-Key'), { target: { value: 'X-Api-Key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(lib.addCredential).toHaveBeenCalledWith({
      name: 'stripe', kind: 'token', description: '', header: 'X-Api-Key',
      allowed_hosts: ['api.stripe.com', 'files.stripe.com'], value: 'sk_test_123',
    }));
  });

  it('editing without a new token keeps the saved one', async () => {
    lib.updateCredential.mockResolvedValue(github);
    render(<CredentialsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lib.updateCredential).toHaveBeenCalledWith('c1', {
      description: 'GitHub API', allowed_hosts: ['api.github.com'], header: '',
    }));
  });
});
