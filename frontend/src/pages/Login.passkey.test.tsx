import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import Login from './Login';

const { get, post, login, onDesktop, openExternal } = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), login: vi.fn(), onDesktop: { value: false }, openExternal: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get, post },
}));
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: () => onDesktop.value,
  desktop: { openExternal },
}));
vi.mock('@/context/AppContext', () => ({ useUser: () => ({ login }) }));
vi.mock('@/components/OAuthButtons', () => ({ default: ({ first }: { first?: ReactNode }) => <div>{first}</div> }));
vi.mock('@/components/ThemeToggle', () => ({ ThemeToggle: () => null }));

const bytes = (...b: number[]) => new Uint8Array(b).buffer;
const credentialsGet = vi.fn();

beforeEach(() => {
  [get, post, login, openExternal, credentialsGet].forEach((fn) => fn.mockReset());
  onDesktop.value = false;
  get.mockResolvedValue({ data: { signup_open: false, code_sign_in: true, passkeys: true, passkey_handoff: false } });
  Object.defineProperty(window, 'PublicKeyCredential', { value: function PublicKeyCredential() {}, configurable: true });
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
  Object.defineProperty(navigator, 'credentials', { value: { get: credentialsGet, create: vi.fn() }, configurable: true });
});

afterEach(() => {
  delete (window as { PublicKeyCredential?: unknown }).PublicKeyCredential;
});

const show = () => render(<MemoryRouter><Login /></MemoryRouter>);

describe('Sign in with a passkey', () => {
  it('asks the browser for a passkey and signs in with it', async () => {
    credentialsGet.mockResolvedValue({
      id: 'q-8', rawId: bytes(0xab, 0xff), type: 'public-key', authenticatorAttachment: 'platform',
      getClientExtensionResults: () => ({}),
      response: { clientDataJSON: bytes(1, 2, 3), authenticatorData: bytes(4), signature: bytes(5, 6), userHandle: bytes(55) },
    });
    post.mockImplementation(async (url: string) => {
      if (url === '/auth/passkey-sign-in/options') {
        return { data: { ceremony: 'c1', options: { challenge: 'AQID', rpId: 'localhost', allowCredentials: [] } } };
      }
      return { data: { user: { email: 'ada@example.com' }, access_token: 'tok' } };
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with a passkey' }));
    await waitFor(() => expect(login).toHaveBeenCalledWith({ email: 'ada@example.com' }, 'tok'));
    const options = credentialsGet.mock.calls[0][0].publicKey;
    expect(new Uint8Array(options.challenge)).toEqual(new Uint8Array([1, 2, 3]));
    expect(post).toHaveBeenLastCalledWith('/auth/passkey-sign-in', {
      ceremony: 'c1',
      credential: expect.objectContaining({
        rawId: 'q_8', type: 'public-key',
        response: { clientDataJSON: 'AQID', authenticatorData: 'BA', signature: 'BQY', userHandle: 'Nw' },
      }),
    });
  });

  it('says nothing when the person closes the prompt', async () => {
    credentialsGet.mockRejectedValue(new DOMException('closed', 'NotAllowedError'));
    post.mockResolvedValue({ data: { ceremony: 'c1', options: { challenge: 'AQID', rpId: 'localhost' } } });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with a passkey' }));
    await waitFor(() => expect(credentialsGet).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it('has no passkey button when the server turned passkeys off', async () => {
    get.mockResolvedValue({ data: { signup_open: false, code_sign_in: true, passkeys: false } });
    show();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Sign in with a passkey' })).not.toBeInTheDocument();
  });

  it('finishes in the browser in the desktop app', async () => {
    onDesktop.value = true;
    get.mockResolvedValue({ data: { signup_open: false, code_sign_in: true, passkeys: true, passkey_handoff: true } });
    post.mockImplementation(async (url: string) => {
      if (url === '/auth/passkey-sign-in/handoff') {
        return { data: { handoff: 'h1', secret: 's1', path: '/passkey?handoff=h1', expires_in: 300 } };
      }
      return { data: { status: 'done', kind: 'sign_in', user: { email: 'ada@example.com' }, access_token: 'tok' } };
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with a passkey' }));
    expect(await screen.findByText('Finish in your browser')).toBeInTheDocument();
    expect(openExternal).toHaveBeenCalledWith(expect.stringMatching(/^http:\/\/localhost(:\d+)?\/passkey\?handoff=h1$/));
    expect(credentialsGet).not.toHaveBeenCalled();
    await waitFor(() => expect(login).toHaveBeenCalledWith({ email: 'ada@example.com' }, 'tok'), { timeout: 4000 });
    expect(post).toHaveBeenCalledWith('/auth/passkeys/handoff/collect', { secret: 's1' });
  });
});
