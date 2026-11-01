import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatChannelsCard } from './ChatChannelsCard';
import type { ChatChannel } from '@/lib/channels';

const channels = vi.hoisted(() => ({
  listChannels: vi.fn(),
  saveChannel: vi.fn(),
  relinkChannel: vi.fn(),
  testChannel: vi.fn(),
  deleteChannel: vi.fn(),
}));

vi.mock('@/lib/channels', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/channels')>()),
  ...channels,
}));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: vi.fn().mockResolvedValue({ data: [{ id: 'a1', name: 'Helper' }] }) },
}));

const empty = (kind: 'telegram' | 'slack'): ChatChannel => ({
  kind, configured: false, enabled: false, linked: false, bot_token_hint: null, app_token_set: false,
  bot_name: '', agent_id: '', send_alerts: true, send_approvals: true, peer_name: '', linked_at: '',
  link_code: '', link_url: '', status: '', last_error: '', connected: false,
});

const waiting: ChatChannel = {
  ...empty('telegram'), configured: true, enabled: true, bot_token_hint: '…oken', bot_name: '@cog_bot',
  link_code: 'ABCDEFGHJK', link_url: 'https://t.me/cog_bot?start=ABCDEFGHJK', status: 'connected', connected: true,
};

beforeEach(() => {
  Object.values(channels).forEach((fn) => fn.mockReset());
  channels.listChannels.mockResolvedValue({ available: true, channels: [empty('telegram'), empty('slack')], slack_manifest: {} });
});

describe('ChatChannelsCard', () => {
  it('connects Telegram with a token and then shows the link code', async () => {
    channels.saveChannel.mockResolvedValue(waiting);
    render(<ChatChannelsCard />);
    fireEvent.change(await screen.findByLabelText('Telegram bot token'), { target: { value: '123:token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect Telegram' }));

    await waitFor(() => expect(channels.saveChannel).toHaveBeenCalledWith('telegram', { bot_token: '123:token' }));
    expect(await screen.findByLabelText('Link code')).toHaveTextContent('ABCDEFGHJK');
    expect(screen.getByRole('link', { name: 'Open @cog_bot in Telegram' })).toHaveAttribute('href', waiting.link_url);
  });

  it('asks Slack for both tokens', async () => {
    render(<ChatChannelsCard />);
    fireEvent.change(await screen.findByLabelText('Slack bot token'), { target: { value: 'xoxb-1' } });
    expect(screen.getByRole('button', { name: 'Connect Slack' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Slack app-level token'), { target: { value: 'xapp-1' } });
    expect(screen.getByRole('button', { name: 'Connect Slack' })).toBeEnabled();
  });

  it('shows a linked chat with its errors and toggles', async () => {
    channels.listChannels.mockResolvedValue({
      available: true,
      channels: [{ ...waiting, link_code: '', linked: true, peer_name: 'Ada', connected: false, status: 'error', last_error: 'Telegram rejected the bot token' }, empty('slack')],
      slack_manifest: {},
    });
    channels.saveChannel.mockResolvedValue({ ...waiting, linked: true, send_alerts: false });
    render(<ChatChannelsCard />);
    expect(await screen.findByText('Ada')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('rejected the bot token');
    fireEvent.click(screen.getByLabelText('Telegram agent alerts'));
    await waitFor(() => expect(channels.saveChannel).toHaveBeenCalledWith('telegram', { send_alerts: false }));
  });
});
