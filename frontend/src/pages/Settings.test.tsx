import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '@/context/ThemeContext';
import Settings from './Settings';
import { isSendKey } from '@/lib/preferences';

const keys = vi.hoisted(() => ({
  listProviderKeys: vi.fn(),
  saveProviderKey: vi.fn(),
  removeProviderKey: vi.fn(),
  testProviderKey: vi.fn(),
}));

vi.mock('@/lib/providerKeys', () => keys);

vi.mock('@/context/AppContext', () => ({
  useUser: () => ({ user: { id: 'u1', name: 'Ada', email: 'ada@example.com' }, logout: vi.fn() }),
}));

vi.mock('@/hooks/useSystemInfo', () => ({
  useSystemInfo: () => ({
    info: { version: '0.4.0', database: 'sqlite', task_queue: 'filesystem', worker_running: false, data_folder: '/data', working_folder: '/work' },
    reload: vi.fn(),
  }),
}));

// Heavy cards are covered by their own tests.
vi.mock('@/components/McpServersCard', () => ({ McpServersCard: () => <p>MCP card</p> }));
vi.mock('@/components/CredentialsCard', () => ({ CredentialsCard: () => <p>Credentials card</p> }));
vi.mock('@/components/ChatChannelsCard', () => ({ ChatChannelsCard: () => <p>Chat apps card</p> }));
vi.mock('@/components/VoiceSettingsCard', () => ({ VoiceSettingsCard: () => <p>Voice card</p> }));

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.hash}</span>;
}

function renderAt(path: string) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<><Settings /><Where /></>} />
          <Route path="/settings/:section" element={<><Settings /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const row = (id: string, source: string, extra = {}) => ({ id, source, env_var: `${id.toUpperCase()}_API_KEY`, hint: null, base_url: '', ...extra });

beforeEach(() => {
  Object.values(keys).forEach((fn) => fn.mockReset());
  keys.listProviderKeys.mockResolvedValue({
    can_edit: true,
    providers: [row('openai', 'env'), row('groq', 'none'), row('openrouter', 'saved', { hint: '…3f2a' })],
  });
  localStorage.clear();
});

describe('Settings', () => {
  it('has a section menu and opens each section at its own address', async () => {
    renderAt('/settings/appearance');
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getByRole('link', { name: /Appearance/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('heading', { name: 'Appearance' })).toBeInTheDocument();

    fireEvent.click(within(nav).getByRole('link', { name: /Agent access/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/settings/access');
    expect(await screen.findByText('MCP card')).toBeInTheDocument();
    expect(screen.getByText('/work')).toBeInTheDocument();
  });

  it('hides desktop-only sections on the web and calls the engine a server', () => {
    renderAt('/settings/account');
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).queryByRole('link', { name: /Desktop app/ })).not.toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: /Server/ })).toHaveAttribute('href', '/settings/engine');
  });

  it('sends old #anchor links to the right section', async () => {
    renderAt('/settings#mcp-servers');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/settings/access#mcp-servers'));
  });

  it('redirects unknown sections to the menu', async () => {
    renderAt('/settings/nope');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/settings$/));
  });

  it('filters the menu with search, including words not in the titles', () => {
    renderAt('/settings/account');
    fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'telegram' } });
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Chat apps›']);
    fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'zzz' } });
    expect(screen.getByText(/No settings match/)).toBeInTheDocument();
  });

  it('shows where each provider key comes from and saves a new one', async () => {
    keys.saveProviderKey.mockResolvedValue(row('groq', 'saved', { hint: '…9876' }));
    keys.testProviderKey.mockResolvedValue({ ok: true, message: 'Works. 12 models available.' });
    renderAt('/settings/models');

    expect(await screen.findByText('FROM .ENV')).toBeInTheDocument();
    expect(screen.getByText('OPENAI_API_KEY')).toBeInTheDocument();
    expect(screen.getByText('…3f2a')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Add key' }));
    fireEvent.change(screen.getByLabelText('Groq API key'), { target: { value: 'gsk-new-9876' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));

    await waitFor(() => expect(keys.saveProviderKey).toHaveBeenCalledWith('groq', 'gsk-new-9876'));
    expect(await screen.findByText('Works. 12 models available.')).toBeInTheDocument();
    expect(screen.getByText('…9876')).toBeInTheDocument();
  });

  it('hides key editing from people who are not the admin', async () => {
    keys.listProviderKeys.mockResolvedValue({ can_edit: false, providers: [row('groq', 'none'), row('openrouter', 'saved', { hint: '…3f2a' })] });
    renderAt('/settings/models');
    expect(await screen.findByText(/Only the admin can change these keys/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add key' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
  });

  it('lets you pick system, light or dark theme and the send key', () => {
    renderAt('/settings/appearance');
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(document.documentElement).toHaveClass('dark');
    expect(localStorage.getItem('darkMode')).toBe('true');
    fireEvent.click(screen.getByRole('radio', { name: 'System' }));
    expect(localStorage.getItem('darkMode')).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: 'Ctrl+Enter' }));
    expect(localStorage.getItem('sendKey')).toBe('ctrl-enter');
    expect(screen.getByRole('radio', { name: 'Ctrl+Enter' })).toHaveAttribute('aria-checked', 'true');
  });
});

describe('isSendKey', () => {
  const press = (over: Partial<{ key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }>) =>
    ({ key: 'Enter', shiftKey: false, ctrlKey: false, metaKey: false, ...over });

  it('sends on Enter by default and on Ctrl/Cmd+Enter when chosen', () => {
    expect(isSendKey(press({}), 'enter')).toBe(true);
    expect(isSendKey(press({ shiftKey: true }), 'enter')).toBe(false);
    expect(isSendKey(press({}), 'ctrl-enter')).toBe(false);
    expect(isSendKey(press({ ctrlKey: true }), 'ctrl-enter')).toBe(true);
    expect(isSendKey(press({ metaKey: true }), 'ctrl-enter')).toBe(true);
    expect(isSendKey(press({ key: 'a' }), 'enter')).toBe(false);
  });
});
