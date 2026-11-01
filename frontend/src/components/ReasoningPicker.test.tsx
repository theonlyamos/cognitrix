import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ReasoningPicker from './ReasoningPicker';

describe('ReasoningPicker', () => {
  it('shows the current level and picks a new one by keyboard', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ReasoningPicker value="low" onChange={onChange} />);
    const button = screen.getByRole('button', { name: /^Reasoning level/ });
    expect(button).toHaveAccessibleName('Reasoning level: low');

    button.focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('option', { name: 'low' })).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowDown}{Enter}');
    expect(onChange).toHaveBeenCalledWith('medium');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });

  it('closes on Escape without changing the level', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ReasoningPicker value="" onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /^Reasoning level/ }));
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
