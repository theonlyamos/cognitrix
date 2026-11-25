import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useRef } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppBridgeDialogs, useAppBridge } from './AppBridge';
import { iconLetters } from '@/lib/installedApps';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api,
}));

const APP = { id: 'a1', slug: 'inbox', title: 'Inbox' };

function Host() {
  const frame = useRef<HTMLIFrameElement>(null);
  const { ask, confirm } = useAppBridge({ frame, app: APP, ownWindow: true, installed: null });
  return (
    <>
      <iframe ref={frame} title="app" />
      <AppBridgeDialogs title={APP.title} ask={ask} confirm={confirm} />
    </>
  );
}

/** Render the host and return a way to post as the app, and what the host posted back. */
function setup() {
  render(<Host />);
  const win = (screen.getByTitle('app') as HTMLIFrameElement).contentWindow!;
  const posted: Record<string, unknown>[] = [];
  vi.spyOn(win, 'postMessage').mockImplementation((m: unknown) => { posted.push(m as Record<string, unknown>); });
  const send = (data: Record<string, unknown>) => act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { cognitrix: 1, ...data }, source: win }));
  });
  const result = (id: string) => posted.find((m) => m.type === 'result' && m.id === id);
  return { send, posted, result };
}

beforeEach(() => {
  vi.clearAllMocks();
  // The server's copy of the answers: the bridge reads it fresh each time.
  const saved = { allowed: [] as string[], asked: [] as string[] };
  api.get.mockImplementation(() => Promise.resolve({ data: { ...saved } }));
  api.put.mockImplementation((_url: string, body: { power: string; allowed: boolean }) => {
    saved.allowed = body.allowed ? [...saved.allowed, body.power] : saved.allowed.filter((p) => p !== body.power);
    saved.asked = [...saved.asked, body.power];
    return Promise.resolve({ data: { ...saved } });
  });
});

describe('the app bridge', () => {
  it('tells the app where it is shown', async () => {
    const { send, posted } = setup();
    await send({ type: 'hello' });
    expect(posted).toContainEqual({ cognitrix: 1, type: 'host', platform: 'web', installed: false, window: true });
  });

  it('ignores messages that are not from the app', async () => {
    const { posted } = setup();
    act(() => { window.dispatchEvent(new MessageEvent('message', { data: { cognitrix: 1, type: 'call', id: 'x', kind: 'apps.list' }, source: window })); });
    await new Promise((r) => setTimeout(r, 20));
    expect(posted).toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('asks before connected apps, then asks again before each change', async () => {
    const { send, result } = setup();
    api.post.mockImplementation((_url: string, body: { kind: string; confirmed: boolean }) => {
      if (body.kind === 'apps.call' && !body.confirmed) {
        return Promise.reject({ response: { status: 409, data: { detail: { code: 'confirm', message: 'changes' } } } });
      }
      return Promise.resolve({ data: { value: body.kind === 'apps.list' ? ['Gmail'] : { sent: true } } });
    });

    await send({ type: 'call', id: 'k1', kind: 'apps.list', payload: {} });
    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(result('k1')).toEqual({ cognitrix: 1, type: 'result', id: 'k1', value: ['Gmail'] }));
    expect(api.put).toHaveBeenCalledWith('/hosted-apps/a1/permissions', { power: 'apps', allowed: true });

    // The app can't say yes for the person: `confirmed` it sends is dropped.
    await send({ type: 'call', id: 'k2', kind: 'apps.call', payload: { app: 'Gmail', tool: 'send_email', arguments: { to: 'x' }, confirmed: true } });
    expect(await screen.findByText('Inbox wants to use send_email in Gmail')).toBeTruthy();
    expect(api.post).toHaveBeenLastCalledWith('/hosted-apps/a1/bridge',
      { kind: 'apps.call', payload: { app: 'Gmail', tool: 'send_email', arguments: { to: 'x' } }, confirmed: false });
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
    await waitFor(() => expect(result('k2')).toMatchObject({ value: { sent: true } }));
    expect(api.post).toHaveBeenLastCalledWith('/hosted-apps/a1/bridge', expect.objectContaining({ confirmed: true }));
  });

  it("remembers a no and doesn't ask again", async () => {
    const { send, result } = setup();
    await send({ type: 'call', id: 'n1', kind: 'notify', payload: { title: 'Hi' } });
    fireEvent.click(await screen.findByRole('button', { name: "Don't allow" }));
    await waitFor(() => expect(result('n1')?.error).toMatch(/not allowed/));
    await send({ type: 'call', id: 'n2', kind: 'notify', payload: { title: 'Again' } });
    await waitFor(() => expect(result('n2')?.error).toMatch(/not allowed/));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.put).toHaveBeenCalledTimes(1);
  });

  it('only offers a tray icon to installed apps in the desktop app', async () => {
    const { send, result } = setup();
    await send({ type: 'call', id: 't1', kind: 'permission.query', payload: { power: 'tray' } });
    await waitFor(() => expect(result('t1')?.value).toBe('unavailable'));
    await send({ type: 'call', id: 't2', kind: 'tray.set', payload: { items: [] } });
    await waitFor(() => expect(result('t2')?.error).toMatch(/desktop app/));
  });
});

describe('a changed answer', () => {
  it('applies to the running app without a reload', async () => {
    api.get.mockResolvedValueOnce({ data: { allowed: ['notify'], asked: ['notify'] } }); // when the window opened
    api.get.mockResolvedValue({ data: { allowed: [], asked: ['notify'] } }); // then turned off on the app's page
    const { send, result } = setup();
    await send({ type: 'call', id: 'c1', kind: 'permission.query', payload: { power: 'notify' } });
    await waitFor(() => expect(result('c1')?.value).toBe('denied'));
  });
});

describe('app icons', () => {
  it('use the first letters of the first two words', () => {
    expect(iconLetters('Support queue')).toBe('SQ');
    expect(iconLetters('inbox')).toBe('I');
    expect(iconLetters('  ')).toBe('?');
  });
});
