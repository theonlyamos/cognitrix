import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ModelPicker, { clearModelCache } from '@/components/ModelPicker';

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ api: { get: apiGet } }));

function Harness({ provider = 'openai', initial = 'gpt-5.5' }: { provider?: string; initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <ModelPicker provider={provider} value={value} onChange={setValue} ariaLabel="Model" />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe('ModelPicker', () => {
  beforeEach(() => {
    clearModelCache();
    apiGet.mockReset().mockResolvedValue({ data: { models: ['gpt-5.5', 'gpt-5-account-only'], live: true } });
  });

  it('lists recent models, then models from the account, then Custom', async () => {
    render(<Harness />);
    const select = screen.getByRole('combobox', { name: 'Model' });
    expect(select).toHaveValue('gpt-5.5');
    expect(screen.getByRole('group', { name: 'Recent' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'gpt-5-account-only' })).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith('/providers/openai/models');
    // Recent models aren't repeated in the account group.
    expect(screen.getAllByRole('option', { name: 'gpt-5.5' })).toHaveLength(1);
    expect(screen.getByRole('option', { name: 'Custom…' })).toBeInTheDocument();
  });

  it('takes any model name through Custom', async () => {
    render(<Harness />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Model' }), '__custom__');
    const input = screen.getByLabelText('Custom model name');
    await userEvent.type(input, 'my-finetune');
    expect(screen.getByTestId('value')).toHaveTextContent('my-finetune');
  });

  it('shows a saved model it does not know as a custom name', async () => {
    render(<Harness initial="old-model-1" />);
    expect(screen.getByLabelText('Custom model name')).toHaveValue('old-model-1');
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
  });

  it('still works when the live list fails', async () => {
    apiGet.mockRejectedValue(new Error('offline'));
    render(<Harness />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('gpt-5.5');
  });
});
