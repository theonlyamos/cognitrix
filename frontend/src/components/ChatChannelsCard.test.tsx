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

const empty = (kind: 'telegram' | 'slack' | 'sms'): ChatChannel => ({
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
  it('shows one compact row per app with setup hidden until asked', async () => {
    render(<ChatChannelsCard />);
    expect(await screen.findByRole('button', { name: 'Set up Telegram' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: 'Set up Slack' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Telegram bot token')).not.toBeInTheDocument();
  });

  it('connects Telegram with a token and then shows the link code', async () => {
    channels.saveChannel.mockResolvedValue(waiting);
    render(<ChatChannelsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Set up Telegram' }));
    fireEvent.change(await screen.findByLabelText('Telegram bot token'), { target: { value: '123:token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect Telegram' }));

    await waitFor(() => expect(channels.saveChannel).toHaveBeenCalledWith('telegram', { bot_token: '123:token' }));
    expect(await screen.findByLabelText('Link code')).toHaveTextContent('ABCDEFGHJK');
    expect(screen.getByRole('link', { name: 'Open @cog_bot in Telegram' })).toHaveAttribute('href', waiting.link_url);
  });

  it('asks Slack for both tokens', async () => {
    render(<ChatChannelsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Set up Slack' }));
    fireEvent.change(await screen.findByLabelText('Slack bot token'), { target: { value: 'xoxb-1' } });
    expect(screen.getByRole('button', { name: 'Connect Slack' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Slack app-level token'), { target: { value: 'xapp-1' } });
    expect(screen.getByRole('button', { name: 'Connect Slack' })).toBeEnabled();
  });

  it('connects a Twilio number for SMS', async () => {
    channels.listChannels.mockResolvedValue({ available: true, channels: [empty('sms')], slack_manifest: {} });
    channels.saveChannel.mockResolvedValue({ ...empty('sms'), configured: true, enabled: true, bot_name: '+15550102000',
      link_code: 'ABCDEFGHJK', link_url: 'sms:+15550102000?body=ABCDEFGHJK', webhook_path: '/api/v1/channels/sms/c1/webhook' });
    render(<ChatChannelsCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Set up SMS' }));
    fireEvent.change(await screen.findByLabelText('Twilio Account SID'), { target: { value: 'AC123' } });
    fireEvent.change(screen.getByLabelText('Twilio Auth Token'), { target: { value: 'tok' } });
    expect(screen.getByRole('button', { name: 'Connect SMS' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Twilio phone number'), { target: { value: '+15550102000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect SMS' }));
    await waitFor(() => expect(channels.saveChannel).toHaveBeenCalledWith('sms', { bot_token: 'tok', app_id: 'AC123', phone_number: '+15550102000' }));
    expect(await screen.findByLabelText('Messaging endpoint')).toHaveTextContent('/api/v1/channels/sms/c1/webhook');
    expect(screen.getByRole('link', { name: 'Text +15550102000' })).toHaveAttribute('href', 'sms:+15550102000?body=ABCDEFGHJK');
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
