import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatModelPicker, { clearChatProviderCache, shortModel } from './ChatModelPicker';
import { clearModelCache } from './ModelPicker';

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ api: { get: apiGet } }));

const LLM = { provider: 'openai', model: 'gpt-6.1-sol' };

describe('ChatModelPicker', () => {
  beforeEach(() => {
    clearChatProviderCache();
    clearModelCache();
    apiGet.mockReset().mockImplementation(async (url: string) => {
      if (url === '/providers/chat-options') {
        return { data: { providers: [{ id: 'openai', default_model: 'gpt-6.1-sol' }, { id: 'anthropic', default_model: 'claude-sonnet-5-5' }] } };
      }
      return { data: { models: [], live: false } };
    });
  });

  it('shows the agent model and picks another provider and model', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ChatModelPicker agentLlm={LLM} value={null} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: 'Model: gpt-6.1-sol (agent default). Change' }));
    await user.click(await screen.findByRole('tab', { name: 'Anthropic' }));
    await user.click(await screen.findByRole('option', { name: 'claude-opus-5-5' }));
    expect(onChange).toHaveBeenCalledWith({ provider: 'anthropic', model: 'claude-opus-5-5' });
  });

  it('treats the agent model as no override and takes a typed model', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ChatModelPicker agentLlm={LLM} value={{ provider: 'openai', model: 'gpt-5.5' }} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: 'Model: gpt-5.5. Change' }));
    await user.click(screen.getByRole('option', { name: 'gpt-6.1-sol' }));
    expect(onChange).toHaveBeenLastCalledWith(null);
    await user.click(screen.getByRole('button', { name: 'Model: gpt-5.5. Change' }));
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/providers/chat-options'));
    await user.type(screen.getByLabelText('Other model name'), 'gpt-next{Enter}');
    expect(onChange).toHaveBeenLastCalledWith({ provider: 'openai', model: 'gpt-next' });
  });

  it('is hidden for agents on a coding agent CLI', () => {
    const { container } = render(<ChatModelPicker agentLlm={{ provider: 'claude-code', model: 'sonnet' }} value={null} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shortens provider-prefixed names', () => {
    expect(shortModel('anthropic/claude-sonnet-5.5')).toBe('claude-sonnet-5.5');
  });
});
