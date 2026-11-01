import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AssistantSetup from './AssistantSetup';
import { AGENTS_CHANGED_EVENT } from '@/lib/providers';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  api: { get: apiGet, post: apiPost },
  API_BASE: '/api/v1',
  errorMessage: (_e: unknown, fallback: string) => fallback,
}));

const status = (over: Record<string, unknown> = {}) => ({
  needs_setup: true,
  has_agents: false,
  suggested: { name: 'Assistant', provider: 'anthropic', model: 'claude-sonnet-5-5' },
  providers: [
    { id: 'anthropic', needs_key: true, has_key: true, default_model: 'claude-sonnet-5-5' },
    { id: 'openai', needs_key: true, has_key: false, default_model: 'gpt-5.5' },
    { id: 'ollama', needs_key: false, has_key: false, default_model: 'llama3.2' },
  ],
  ...over,
});

function renderSetup() {
  return render(
    <MemoryRouter>
      <AssistantSetup />
    </MemoryRouter>,
  );
}

describe('first-run assistant setup', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    localStorage.clear();
  });

  it('stays hidden once setup is done', async () => {
    apiGet.mockResolvedValue({ data: status({ needs_setup: false }) });
    renderSetup();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/onboarding'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('creates the assistant with the chosen name and model, then selects it', async () => {
    apiGet.mockResolvedValue({ data: status() });
    apiPost.mockResolvedValue({ data: { agent: { id: 'a1' } } });
    const changed = vi.fn();
    window.addEventListener(AGENTS_CHANGED_EVENT, changed);
    renderSetup();

    const name = await screen.findByLabelText(/NAME/);
    fireEvent.change(name, { target: { value: 'Jarvis' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create assistant' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(apiPost).toHaveBeenCalledWith('/onboarding/assistant', {
      name: 'Jarvis', provider: 'anthropic', model: 'claude-sonnet-5-5',
    });
    expect(localStorage.getItem('selectedAgentId')).toBe('a1');
    expect(changed).toHaveBeenCalled();
    window.removeEventListener(AGENTS_CHANGED_EVENT, changed);
  });

  it('asks for a key only when the provider has none', async () => {
    apiGet.mockResolvedValue({ data: status() });
    renderSetup();
    const provider = await screen.findByLabelText(/PROVIDER/);
    expect(screen.queryByLabelText(/API KEY/)).not.toBeInTheDocument();

    fireEvent.change(provider, { target: { value: 'openai' } });
    expect(screen.getByLabelText(/MODEL/)).toHaveValue('gpt-5.5');
    fireEvent.click(screen.getByRole('button', { name: 'Create assistant' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/API key/);
    expect(apiPost).not.toHaveBeenCalled();

    apiPost.mockResolvedValue({ data: { agent: { id: 'a2' } } });
    fireEvent.change(screen.getByLabelText(/API KEY/), { target: { value: 'sk-mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create assistant' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/onboarding/assistant', {
      name: 'Assistant', provider: 'openai', model: 'gpt-5.5', api_key: 'sk-mine',
    }));
  });

  it('skips setup', async () => {
    apiGet.mockResolvedValue({ data: status() });
    apiPost.mockResolvedValue({ data: { agent: { id: 'a3' } } });
    renderSetup();
    fireEvent.click(await screen.findByRole('button', { name: 'Skip and use defaults' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/onboarding/skip'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
