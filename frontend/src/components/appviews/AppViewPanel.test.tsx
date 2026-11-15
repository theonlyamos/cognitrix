import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppViewPanel } from './AppViewPanel';
import { AppViewCard } from './AppViewCard';
import { announceAppViewUpdate, appViewFrom, onOpenAppView, safeLink } from '@/lib/appViews';

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: apiGet, post: apiPost },
}));
vi.mock('@/context/ThemeContext', () => ({ useTheme: () => ({ theme: 'dark' }) }));

const VIEW = {
  id: 'v1', app: 'Tasks', tool: 'add_task', title: 'Tasks: Add task', session_id: 's1', agent_id: 'a1', version: 1,
  created: '', updated: '', tool_input: { text: 'One' },
  tool_result: { content: [{ type: 'text', text: '{}' }], structuredContent: { tasks: ['One'] } },
  frame_url: '/api/v1/app-view-frame/tok', prefers_border: false, error: null,
};

/** Messages the panel posted into the frame. */
function frameMessages() {
  const frame = document.querySelector('iframe')!;
  const sent: Record<string, unknown>[] = [];
  vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation((message: unknown) => { sent.push(message as Record<string, unknown>); });
  const fromFrame = (data: Record<string, unknown>) => act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { jsonrpc: '2.0', ...data }, source: frame.contentWindow }));
  });
  return { sent, fromFrame };
}

describe('AppViewPanel', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockResolvedValue({ data: VIEW });
  });

  it('answers initialize, then sends the tool input and result', async () => {
    render(<AppViewPanel viewId="v1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    const { sent, fromFrame } = frameMessages();
    expect(document.querySelector('iframe')!.getAttribute('sandbox')).not.toContain('allow-same-origin');

    await fromFrame({ id: 1, method: 'ui/initialize', params: { protocolVersion: '2026-01-26' } });
    const init = sent.find((m) => m.id === 1) as { result: { protocolVersion: string; hostContext: { theme: string } } };
    expect(init.result.protocolVersion).toBe('2026-01-26');
    expect(init.result.hostContext.theme).toBe('dark');
    expect(sent.some((m) => m.method === 'ui/notifications/tool-result')).toBe(false);

    await fromFrame({ method: 'ui/notifications/initialized' });
    const methods = sent.map((m) => m.method).filter(Boolean);
    expect(methods).toEqual(['ui/notifications/tool-input', 'ui/notifications/tool-result']);
    expect((sent.find((m) => m.method === 'ui/notifications/tool-result') as { params: unknown }).params).toEqual(VIEW.tool_result);

    // The agent runs the tool again: the new result goes into the running screen.
    apiGet.mockResolvedValue({ data: { ...VIEW, version: 2, tool_input: { text: 'Two' }, tool_result: { content: [], structuredContent: { tasks: ['One', 'Two'] } } } });
    act(() => announceAppViewUpdate('v1'));
    await waitFor(() => expect(sent.filter((m) => m.method === 'ui/notifications/tool-result')).toHaveLength(2));
  });

  it('asks the person before a tool that changes things, and says no when they do', async () => {
    render(<AppViewPanel viewId="v1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    const { sent, fromFrame } = frameMessages();
    apiPost.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'confirm' } } });
    await fromFrame({ id: 7, method: 'tools/call', params: { name: 'add_task', arguments: { text: 'Two' } } });
    expect(await screen.findByRole('alertdialog')).toHaveTextContent('Let Tasks run add task?');

    apiPost.mockResolvedValueOnce({ data: { content: [], structuredContent: { ok: true } } });
    fireEvent.click(screen.getByRole('button', { name: 'Run it' }));
    await waitFor(() => expect(sent.find((m) => m.id === 7)).toEqual({ jsonrpc: '2.0', id: 7, result: { content: [], structuredContent: { ok: true } } }));
    expect(apiPost).toHaveBeenLastCalledWith('/app-views/v1/call', { name: 'add_task', arguments: { text: 'Two' }, confirmed: true });

    apiPost.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'confirm' } } });
    await fromFrame({ id: 8, method: 'tools/call', params: { name: 'clear_board', arguments: {} } });
    fireEvent.click(await screen.findByRole('button', { name: "Don't run" }));
    await waitFor(() => expect((sent.find((m) => m.id === 8) as { error?: { message: string } })?.error?.message).toBe('The person said no.'));
  });

  it('reports a switched-off app instead of asking to run it', async () => {
    render(<AppViewPanel viewId="v1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    const { sent, fromFrame } = frameMessages();
    apiPost.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'Tasks is switched off.' } } });
    await fromFrame({ id: 9, method: 'tools/call', params: { name: 'show_board', arguments: {} } });
    await waitFor(() => expect((sent.find((m) => m.id === 9) as { error?: unknown })?.error).toBeTruthy());
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('passes messages into the chat, opens only web links, and refuses unknown methods', async () => {
    const onSend = vi.fn();
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<AppViewPanel viewId="v1" onClose={() => {}} onSendMessage={onSend} />);
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    const { sent, fromFrame } = frameMessages();
    await fromFrame({ id: 1, method: 'ui/message', params: { role: 'user', content: [{ type: 'text', text: 'Plan my day' }] } });
    expect(onSend).toHaveBeenCalledWith('Plan my day');
    await fromFrame({ id: 2, method: 'ui/open-link', params: { url: 'javascript:alert(1)' } });
    await fromFrame({ id: 3, method: 'ui/open-link', params: { url: 'https://example.com/a' } });
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer');
    await fromFrame({ id: 4, method: 'sampling/createMessage', params: {} });
    await waitFor(() => expect((sent.find((m) => m.id === 4) as { error: { code: number } }).error.code).toBe(-32601));
    expect((sent.find((m) => m.id === 2) as { error: { code: number } }).error.code).toBe(-32602);

    await fromFrame({ id: 5, method: 'ui/update-model-context', params: { structuredContent: { open: 2 } } });
    expect(apiPost).toHaveBeenCalledWith('/app-views/v1/context', { content: null, structuredContent: { open: 2 } });
    open.mockRestore();
  });

  it('never sends a slow answer to a different app screen', async () => {
    let finish: (value: unknown) => void = () => {};
    apiPost.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const { rerender } = render(<AppViewPanel viewId="v1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    const first = frameMessages();
    await first.fromFrame({ id: 3, method: 'tools/call', params: { name: 'show_board', arguments: {} } });

    apiGet.mockResolvedValue({ data: { ...VIEW, id: 'v2', app: 'Secrets', title: 'Secrets: List', frame_url: '/api/v1/app-view-frame/other' } });
    rerender(<AppViewPanel viewId="v2" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector('iframe')!.getAttribute('src')).toContain('other'));
    const second = frameMessages();
    await act(async () => { finish({ data: { content: [{ type: 'text', text: 'private' }] } }); });
    expect(second.sent.some((m) => m.id === 3)).toBe(false);
    expect(first.sent.some((m) => m.id === 3)).toBe(false);
  });

  it('ignores messages from anywhere but its own frame', async () => {
    const onSend = vi.fn();
    render(<AppViewPanel viewId="v1" onClose={() => {}} onSendMessage={onSend} />);
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    act(() => {
      window.dispatchEvent(new MessageEvent('message', { data: { jsonrpc: '2.0', id: 1, method: 'ui/message', params: { content: [{ type: 'text', text: 'x' }] } }, source: window }));
    });
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe('app view helpers', () => {
  it('finds the screen a tool result opened', () => {
    expect(appViewFrom('Tasks is showing this beside the chat (app_view abc_1, version 3). Run the tool again...\n\n{}'))
      .toEqual({ app: 'Tasks', id: 'abc_1', version: 3 });
    expect(appViewFrom('nothing here')).toBeNull();
    expect(safeLink('https://a.example')).toBe('https://a.example/');
    expect(safeLink('data:text/html,hi')).toBeNull();
  });

  it('the chat card asks the chat to open the screen', () => {
    const opened = vi.fn();
    const stop = onOpenAppView(opened);
    render(<AppViewCard result="Tasks is showing this beside the chat (app_view v9, version 1). Run the tool again." />);
    fireEvent.click(screen.getByRole('button', { name: /Tasks/ }));
    expect(opened).toHaveBeenCalledWith('v9');
    stop();
  });
});
