import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ComposerMenu from './ComposerMenu';

function setup(over: Partial<Parameters<typeof ComposerMenu>[0]> = {}) {
  const props = {
    onAttach: vi.fn(),
    onRunTask: vi.fn(),
    runTaskDisabled: false,
    runTaskTitle: '',
    bypass: false,
    onToggleBypass: vi.fn(),
    incognito: false,
    onToggleIncognito: vi.fn(),
    incognitoDisabled: false,
    incognitoTitle: '',
    reasoning: '',
    onReasoning: vi.fn(),
    ...over,
  };
  render(<ComposerMenu {...props} />);
  return props;
}

describe('ComposerMenu', () => {
  it('opens from the + button and closes after Attach', async () => {
    const user = userEvent.setup();
    const p = setup();
    expect(screen.queryByRole('menu')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'More options' }));
    await user.click(screen.getByRole('menuitem', { name: /Attach files/ }));
    expect(p.onAttach).toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('keeps the menu open while toggling and picking reasoning', async () => {
    const user = userEvent.setup();
    const p = setup();
    await user.click(screen.getByRole('button', { name: 'More options' }));
    await user.click(screen.getByRole('menuitemcheckbox', { name: 'Auto-approve' }));
    await user.click(screen.getByRole('radio', { name: 'medium' }));
    expect(p.onToggleBypass).toHaveBeenCalled();
    expect(p.onReasoning).toHaveBeenCalledWith('medium');
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole('button', { name: 'More options' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('shows tags for options that are on, and the tag turns them off', async () => {
    const user = userEvent.setup();
    const p = setup({ bypass: true, incognito: true, reasoning: 'high' });
    await user.click(screen.getByRole('button', { name: 'Turn off auto-approve' }));
    await user.click(screen.getByRole('button', { name: 'Turn off incognito' }));
    expect(p.onToggleBypass).toHaveBeenCalled();
    expect(p.onToggleIncognito).toHaveBeenCalled();
    expect(screen.getByText('high')).toBeInTheDocument();
  });

  it('shows no tags when everything is off', () => {
    setup();
    expect(screen.queryByRole('button', { name: /Turn off/ })).toBeNull();
    expect(screen.queryByText(/reasoning/)).toBeNull();
  });
});
