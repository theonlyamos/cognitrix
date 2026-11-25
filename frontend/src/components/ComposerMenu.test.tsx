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
    expect(screen.getByRole('button', { name: 'Reasoning high. Change' })).toBeInTheDocument();
  });

  it('shows no tags when everything is off', () => {
    setup();
    expect(screen.queryByRole('button', { name: /Turn off/ })).toBeNull();
    expect(screen.queryByText(/reasoning/)).toBeNull();
  });

  it('on a phone, keeps the chat settings and live voice inside the sheet', async () => {
    const user = userEvent.setup();
    const p = setup({ phone: true, settings: <div><button type="button">Model</button></div>, onLiveVoice: vi.fn(), sandbox: { on: true, available: true, onToggle: vi.fn() } });
    // The row holds only the + button: no settings chip, no sandbox tag.
    expect(screen.queryByRole('button', { name: 'Model' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Turn off run in sandbox' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'More options' }));
    expect(screen.getByText('This chat')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Model' })).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: /Talk out loud/ }));
    expect(p.onLiveVoice).toHaveBeenCalled();
  });

  it('on a wide screen, shows the chat settings beside the + button', () => {
    setup({ settings: <div><button type="button">Model</button></div>, onLiveVoice: vi.fn() });
    expect(screen.getByRole('button', { name: 'Model' })).toBeInTheDocument();
    expect(screen.queryByText('This chat')).toBeNull();
  });

  it('offers fast mode only when given, with a tag that turns it off', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole('button', { name: 'More options' }));
    expect(screen.queryByRole('menuitemcheckbox', { name: 'Fast mode' })).toBeNull();
  });

  it('toggles fast mode from the menu and from its tag', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    setup({ fast: { on: true, onToggle } });
    await user.click(screen.getByRole('button', { name: 'Turn off fast mode' }));
    await user.click(screen.getByRole('button', { name: 'More options' }));
    const item = screen.getByRole('menuitemcheckbox', { name: 'Fast mode' });
    expect(item).toHaveAttribute('aria-checked', 'true');
    await user.click(item);
    expect(onToggle).toHaveBeenCalledTimes(2);
  });
});
