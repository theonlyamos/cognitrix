import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConnectedAppsCard } from './ConnectedAppsCard';
import { signInNeeded } from './SafetyNotices';
import type { AppList, CatalogApp, ProviderSetup } from '@/lib/apps';

const apps = vi.hoisted(() => ({
  listApps: vi.fn(),
  startAppSignIn: vi.fn(),
  startServerSignIn: vi.fn(),
  connectWithToken: vi.fn(),
  disconnectApp: vi.fn(),
  signOutServer: vi.fn(),
  addCustomApp: vi.fn(),
  signInWithBrowser: vi.fn(),
  setAppOptions: vi.fn(),
  testApp: vi.fn(),
  getSetupGuide: vi.fn(),
  saveProviderClient: vi.fn(),
  removeProviderClient: vi.fn(),
  testProviderClient: vi.fn(),
  saveComposioKey: vi.fn(),
  removeComposioKey: vi.fn(),
  searchComposioApps: vi.fn(),
  startComposioSignIn: vi.fn(),
  disconnectComposioApp: vi.fn(),
  testComposioApp: vi.fn(),
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

const slackSetup: ProviderSetup = {
  id: 'slack', label: 'Slack', source: 'none', can_edit: true, client_id: '', env_vars: ['SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET'],
  redirect_uri: 'https://cog.example.com/api/v1/auth/oauth/slack/callback', redirect_problem: null, apps: ['Slack'],
  steps: [
    { title: 'Create a Slack app from this manifest', body: 'Paste this.', copy: { label: 'App manifest', text: '{"display_information":{}}' } },
    { title: 'Save the client ID and secret', body: 'Paste both below.' },
  ],
};

const gmailOn: CatalogApp = {
  ...base,
  options: [{ id: 'send', label: 'Send email', description: 'Agents can send mail. Each send asks you first.', needs_sign_in: false }],
  connection: { server_id: 's-gm', name: 'gmail', url: null, auth_type: 'oauth', status: 'connected', account: 'bob@gmail.com', enabled: true, options: [], pending_options: [] },
};

const calendarOn: CatalogApp = {
  ...base, id: 'google-calendar', name: 'Google Calendar',
  options: [{ id: 'edit', label: 'Create and change events', description: 'Each change asks you first.', needs_sign_in: true }],
  connection: { server_id: 's-cal', name: 'google_calendar', url: null, auth_type: 'oauth', status: 'connected', account: 'bob@gmail.com', enabled: true, options: ['edit'], pending_options: ['edit'] },
};

beforeEach(() => {
  Object.values(apps).forEach((fn) => fn.mockReset());
  apps.listApps.mockResolvedValue(listing());
  apps.getSetupGuide.mockResolvedValue([slackSetup]);
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
    // Admins get a way into the setup steps.
    expect(screen.getByRole('button', { name: 'Set up Google' })).toBeInTheDocument();
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

describe('ConnectedAppsCard extras', () => {
  it('turns an extra permission on and offers to reconnect when it needs a new sign-in', async () => {
    apps.listApps.mockResolvedValue(listing({ apps: [gmailOn, calendarOn], is_admin: false }));
    apps.setAppOptions.mockResolvedValue({});
    apps.startAppSignIn.mockResolvedValue({ flow_id: 'f', authorize_url: 'https://accounts.google.com/o' });
    render(<ConnectedAppsCard />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /Send email/ }));
    await waitFor(() => expect(apps.setAppOptions).toHaveBeenCalledWith('gmail', ['send']));
    expect(screen.getByRole('checkbox', { name: /Create and change events/ })).toBeChecked();
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect to allow' }));
    await waitFor(() => expect(apps.startAppSignIn).toHaveBeenCalledWith('google-calendar'));
    // People who aren't admins never see the setup steps.
    expect(apps.getSetupGuide).not.toHaveBeenCalled();
  });

  it('tests a connection and says what is wrong', async () => {
    apps.listApps.mockResolvedValue(listing({ apps: [gmailOn], is_admin: false }));
    apps.testApp.mockResolvedValue({
      ok: false, tools: 0,
      checks: [
        { label: 'Signed in', ok: true, detail: 'bob@gmail.com' },
        { label: "Gmail's server answers", ok: false, detail: "The Gmail MCP API isn't turned on." },
      ],
    });
    render(<ConnectedAppsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Test Gmail' }));
    const result = await screen.findByRole('status', { name: 'Gmail test' });
    expect(result).toHaveTextContent("Failed: Gmail's server answers");
    expect(result).toHaveTextContent("The Gmail MCP API isn't turned on.");
  });

  it('walks an admin through setting up a provider and checks it', async () => {
    apps.saveProviderClient.mockResolvedValue({ ...slackSetup, source: 'saved', client_id: '12.34' });
    apps.testProviderClient.mockResolvedValue({ ok: true, checks: [{ label: 'Slack accepts the client', ok: true, detail: '' }] });
    render(<ConnectedAppsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Set up Slack' }));
    expect(screen.getByText('Create a Slack app from this manifest')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy app manifest' })).toBeInTheDocument();
    const form = screen.getByRole('form', { name: 'Slack client' });
    const [clientId, secret] = Array.from(form.querySelectorAll('input'));
    fireEvent.change(clientId, { target: { value: '12.34' } });
    fireEvent.change(secret, { target: { value: 'shh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(apps.saveProviderClient).toHaveBeenCalledWith('slack', '12.34', 'shh'));
    expect(await screen.findByRole('status', { name: 'Slack check' })).toHaveTextContent('Passed: Slack accepts the client');
    expect(screen.getByText('ready')).toBeInTheDocument();
  });

  it('keeps Composio off until a key is added, then finds and connects apps', async () => {
    apps.listApps.mockResolvedValue(listing({ is_admin: false, composio: { has_key: false, hint: null, connections: [] } }));
    apps.saveComposioKey.mockResolvedValue({ has_key: true, hint: '…good' });
    render(<ConnectedAppsCard />);
    expect(await screen.findByText(/passes through its\s+servers/)).toBeInTheDocument();
    expect(screen.queryByRole('search')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add a Composio API key' }));
    apps.listApps.mockResolvedValue(listing({ is_admin: false, composio: { has_key: true, hint: '…good', connections: [] } }));
    fireEvent.change(screen.getByRole('form', { name: 'Composio API key' }).querySelector('input')!, { target: { value: 'ck_good' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await waitFor(() => expect(apps.saveComposioKey).toHaveBeenCalledWith('ck_good'));

    apps.searchComposioApps.mockResolvedValue({
      toolkits: [
        { slug: 'hubspot', name: 'HubSpot', description: 'CRM', logo: '', categories: [], tools_count: 12, connectable: true, connection: null },
        { slug: 'acme', name: 'Acme', description: '', logo: '', categories: [], tools_count: 0, connectable: false, connection: null },
      ],
      next_cursor: null,
    });
    fireEvent.change(await screen.findByPlaceholderText('Find an app, e.g. HubSpot'), { target: { value: 'crm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(apps.searchComposioApps).toHaveBeenCalledWith('crm'));
    apps.startComposioSignIn.mockResolvedValue({ flow_id: 'f', authorize_url: 'https://connect.composio.dev/link/abc' });
    fireEvent.click(await screen.findByRole('button', { name: 'Connect HubSpot' }));
    await waitFor(() => expect(apps.startComposioSignIn).toHaveBeenCalledWith('hubspot', 'HubSpot'));
    expect(screen.getByText(/Needs its own sign-in/)).toBeInTheDocument();
  });
});

describe('signInNeeded', () => {
  it('finds the app name in a tool result', () => {
    expect(signInNeeded('App sign-in needed: Google Calendar. Right now the sign-in expired.')).toBe('Google Calendar');
    expect(signInNeeded('All good')).toBeNull();
  });
});
