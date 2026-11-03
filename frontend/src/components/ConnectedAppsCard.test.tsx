import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConnectedAppsCard } from './ConnectedAppsCard';
import { signInNeeded } from './SafetyNotices';
import type { AppList, CatalogApp } from '@/lib/apps';

const apps = vi.hoisted(() => ({
  listApps: vi.fn(),
  startAppSignIn: vi.fn(),
  startServerSignIn: vi.fn(),
  connectWithToken: vi.fn(),
  disconnectApp: vi.fn(),
  signOutServer: vi.fn(),
  addCustomApp: vi.fn(),
  signInWithBrowser: vi.fn(),
}));

vi.mock('@/lib/apps', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/apps')>()),
  ...apps,
}));

const base: CatalogApp = {
  id: 'gmail', name: 'Gmail', description: 'Search and read mail.', category: 'Google', auth: 'provider',
  provider: 'google', available: true, setup: '', token_help: null, connection: null,
};

function listing(overrides: Partial<AppList> = {}): AppList {
  return {
    apps: [
      base,
      {
        ...base, id: 'google-calendar', name: 'Google Calendar',
        connection: { server_id: 's-cal', name: 'google_calendar', url: null, auth_type: 'oauth', status: 'needs_reauth', account: 'bob@gmail.com', enabled: true },
      },
      {
        ...base, id: 'github', name: 'GitHub', category: 'Developer', provider: 'github',
        token_help: 'Or paste a personal access token.',
        connection: { server_id: 's-gh', name: 'github', url: null, auth_type: 'oauth', status: 'connected', account: 'bob', enabled: true },
      },
      { ...base, id: 'google-docs', name: 'Google Docs', available: false, setup: 'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.' },
    ],
    custom: [],
    is_admin: true,
    callback_urls: { google: 'http://127.0.0.1:48210/api/v1/auth/oauth/google/callback' },
    ...overrides,
  };
}

beforeEach(() => {
  Object.values(apps).forEach((fn) => fn.mockReset());
  apps.listApps.mockResolvedValue(listing());
  apps.signInWithBrowser.mockImplementation(async (start: () => Promise<unknown>) => {
    await start();
    return { status: 'done', error: null };
  });
});

describe('ConnectedAppsCard', () => {
  it('shows each app with its state and who it is signed in as', async () => {
    render(<ConnectedAppsCard />);
    expect(await screen.findByText('Gmail')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
    expect(screen.getByText('needs reconnect')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeInTheDocument();
    expect(screen.getByText('Not set up on this server')).toBeInTheDocument();
    // Admins see what to set up, including the redirect URI to register.
    expect(screen.getByText(/127\.0\.0\.1:48210\/api\/v1\/auth\/oauth\/google\/callback/)).toBeInTheDocument();
  });

  it('connects through the browser sign-in and reloads', async () => {
    apps.startAppSignIn.mockResolvedValue({ flow_id: 'f', authorize_url: 'https://accounts.google.com/o' });
    render(<ConnectedAppsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(apps.startAppSignIn).toHaveBeenCalledWith('gmail'));
    await waitFor(() => expect(apps.listApps).toHaveBeenCalledTimes(2));
  });

  it('shows a sign-in failure next to the app', async () => {
    apps.signInWithBrowser.mockResolvedValue({ status: 'error', error: 'Sign-in was cancelled.' });
    render(<ConnectedAppsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign-in was cancelled.');
  });

  it('adds an app by address', async () => {
    apps.addCustomApp.mockResolvedValue({ connection: {}, flow: { flow_id: 'f', authorize_url: 'https://auth.example.com' } });
    render(<ConnectedAppsCard />);
    fireEvent.click(await screen.findByRole('button', { name: '+ Add an app by address' }));
    fireEvent.change(screen.getByPlaceholderText('tracker'), { target: { value: 'tracker' } });
    fireEvent.change(screen.getByPlaceholderText('https://mcp.example.com/mcp'), { target: { value: 'https://mcp.example.com/mcp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(apps.addCustomApp).toHaveBeenCalledWith('tracker', 'https://mcp.example.com/mcp'));
  });
});

describe('signInNeeded', () => {
  it('finds the app name in a tool result', () => {
    expect(signInNeeded('App sign-in needed: Google Calendar. Right now the sign-in expired.')).toBe('Google Calendar');
    expect(signInNeeded('All good')).toBeNull();
  });
});
