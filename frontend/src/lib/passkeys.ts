import { api } from '@/lib/api';
import { desktop, isDesktop } from '@/lib/desktop';

/**
 * Passkeys (cognitrix/passkeys.py). The browser does the WebAuthn step; the
 * server only sees public keys and signatures.
 *
 * The desktop app's window is on http://127.0.0.1, and browsers never allow
 * passkeys on an IP address (macOS and Linux webviews have no WebAuthn for
 * apps like this one at all), so the app does the step in the default
 * browser instead: see startBrowserStep below and pages/PasskeyHandoff.tsx.
 */

export interface Passkey {
  id: string;
  name: string;
  /** 'Windows Hello', 'iCloud Keychain', ... ('' when unknown). */
  provider: string;
  /** The host name it works on. */
  site: string;
  /** Synced to the person's other devices. */
  synced: boolean;
  created_at: string;
  last_used_at: string;
}

export interface PasskeyList {
  passkeys: Passkey[];
  enabled: boolean;
  /** This server is the desktop app's engine: passkey steps go to the browser. */
  handoff: boolean;
  /** An address of this server where passkeys work ('' when there is none). */
  secure_address?: string;
}

interface Ceremony {
  ceremony: string;
  options: Record<string, unknown>;
}

// ---- base64url <-> bytes ------------------------------------------------------

function fromB64(text: string): ArrayBuffer {
  const b64 = text.replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out.buffer;
}

function toB64(buffer: ArrayBuffer | null | undefined): string {
  if (!buffer) return '';
  const bytes = new Uint8Array(buffer);
  let raw = '';
  for (let i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

function creationOptions(o: Json): PublicKeyCredentialCreationOptions {
  return {
    ...o,
    challenge: fromB64(o.challenge),
    user: { ...o.user, id: fromB64(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c: Json) => ({ ...c, id: fromB64(c.id) })),
  } as PublicKeyCredentialCreationOptions;
}

function requestOptions(o: Json): PublicKeyCredentialRequestOptions {
  return {
    ...o,
    challenge: fromB64(o.challenge),
    allowCredentials: (o.allowCredentials ?? []).map((c: Json) => ({ ...c, id: fromB64(c.id) })),
  } as PublicKeyCredentialRequestOptions;
}

function credentialJson(cred: PublicKeyCredential): Json {
  const r = cred.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const response: Json = { clientDataJSON: toB64(r.clientDataJSON) };
  if ('attestationObject' in r && r.attestationObject) {
    response.attestationObject = toB64(r.attestationObject);
    response.transports = typeof r.getTransports === 'function' ? r.getTransports() : [];
  } else {
    response.authenticatorData = toB64(r.authenticatorData);
    response.signature = toB64(r.signature);
    if (r.userHandle) response.userHandle = toB64(r.userHandle);
  }
  return {
    id: cred.id,
    rawId: toB64(cred.rawId),
    type: cred.type,
    response,
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
  };
}

// ---- What this page can do ----------------------------------------------------

function isIpAddress(host: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(':') || host.startsWith('[');
}

/** This page itself can make and use passkeys (not the desktop app's window, not an IP address). */
export function canUsePasskeysHere(): boolean {
  if (typeof window === 'undefined' || isDesktop()) return false;
  if (!window.PublicKeyCredential || !navigator.credentials) return false;
  if (!window.isSecureContext) return false;
  return !isIpAddress(window.location.hostname);
}

/** Opened at an IP address like http://127.0.0.1:8000: the same page at localhost can use passkeys. */
export function localhostAddress(): string {
  if (typeof window === 'undefined') return '';
  const { hostname, port, pathname, protocol } = window.location;
  if (hostname !== '127.0.0.1' && hostname !== '[::1]') return '';
  return `${protocol}//localhost${port ? `:${port}` : ''}${pathname}`;
}

/** `address` when this page is somewhere else than it, '' otherwise. */
export function otherAddress(address: string | undefined): string {
  if (!address || typeof window === 'undefined') return '';
  try {
    return new URL(address).hostname === window.location.hostname ? '' : address.replace(/\/$/, '');
  } catch {
    return '';
  }
}

/** The person closed the prompt or it timed out: not worth an error message. */
export function wasCanceled(err: unknown): boolean {
  return err instanceof DOMException && (err.name === 'NotAllowedError' || err.name === 'AbortError');
}

// ---- Calls ---------------------------------------------------------------------

export async function listPasskeys(): Promise<PasskeyList> {
  return (await api.get('/auth/passkeys')).data;
}

export async function renamePasskey(id: string, name: string): Promise<Passkey> {
  return (await api.patch(`/auth/passkeys/${encodeURIComponent(id)}`, { name })).data;
}

export async function removePasskey(id: string): Promise<void> {
  await api.delete(`/auth/passkeys/${encodeURIComponent(id)}`);
}

async function create(start: Ceremony): Promise<Json> {
  const cred = await navigator.credentials.create({ publicKey: creationOptions(start.options) });
  if (!cred) throw new DOMException('No passkey was made', 'NotAllowedError');
  return credentialJson(cred as PublicKeyCredential);
}

async function get(start: Ceremony, signal?: AbortSignal, conditional = false): Promise<Json> {
  const cred = await navigator.credentials.get({
    publicKey: requestOptions(start.options),
    signal,
    ...(conditional ? { mediation: 'conditional' as CredentialMediationRequirement } : {}),
  });
  if (!cred) throw new DOMException('No passkey was picked', 'NotAllowedError');
  return credentialJson(cred as PublicKeyCredential);
}

/** Add a passkey to the signed-in account, on this page. */
export async function addPasskey(name = ''): Promise<Passkey> {
  const start: Ceremony = (await api.post('/auth/passkeys/register/options')).data;
  const credential = await create(start);
  return (await api.post('/auth/passkeys/register', { ceremony: start.ceremony, credential, name })).data;
}

export interface SignedIn {
  user: Record<string, unknown> & { email: string };
  access_token: string;
}

/**
 * Sign in with a passkey on this page. With `conditional`, the browser offers
 * passkeys in the email field's autofill instead of a dialog, and the promise
 * waits until one is picked (or `signal` aborts it).
 */
export async function signInWithPasskey(opts: { signal?: AbortSignal; conditional?: boolean } = {}): Promise<SignedIn> {
  const start: Ceremony = (await api.post('/auth/passkey-sign-in/options')).data;
  const credential = await get(start, opts.signal, opts.conditional);
  return (await api.post('/auth/passkey-sign-in', { ceremony: start.ceremony, credential })).data;
}

/** The browser offers passkeys in autofill (Chrome, Edge, Safari, Firefox 119+). */
export async function autofillAvailable(): Promise<boolean> {
  if (!canUsePasskeysHere()) return false;
  const pkc = window.PublicKeyCredential as unknown as { isConditionalMediationAvailable?: () => Promise<boolean> };
  try {
    return !!(await pkc.isConditionalMediationAvailable?.());
  } catch {
    return false;
  }
}

// ---- Desktop app: the step in the default browser --------------------------------

export interface BrowserStep {
  handoff: string;
  secret: string;
  path: string;
  expires_in: number;
}

export type BrowserStepStatus =
  | { status: 'waiting' | 'opened'; expires_in: number }
  | { status: 'expired' }
  | ({ status: 'done'; kind: 'sign_in' } & SignedIn)
  | { status: 'done'; kind: 'register'; passkey: Passkey | null };

/**
 * The browser page for a handoff. Always localhost: passkeys need a host
 * name, and the app's own window is on 127.0.0.1.
 */
export function browserStepUrl(step: BrowserStep): string {
  const port = window.location.port ? `:${window.location.port}` : '';
  return `http://localhost${port}${step.path}`;
}

/** Start the step and open the browser on it. */
export async function startBrowserStep(kind: 'sign_in' | 'register'): Promise<BrowserStep> {
  const step: BrowserStep = kind === 'register'
    ? (await api.post('/auth/passkeys/handoff', {})).data
    : (await api.post('/auth/passkey-sign-in/handoff', { device_name: 'Cognitrix desktop app' })).data;
  const url = browserStepUrl(step);
  if (isDesktop()) await desktop.openExternal(url);
  else window.open(url, '_blank', 'noopener');
  return step;
}

export async function browserStepStatus(secret: string): Promise<BrowserStepStatus> {
  return (await api.post('/auth/passkeys/handoff/collect', { secret })).data;
}

export async function cancelBrowserStep(secret: string): Promise<void> {
  await api.post('/auth/passkeys/handoff/cancel', { secret });
}

// ---- The browser page (pages/PasskeyHandoff.tsx) ------------------------------------

export interface HandoffInfo {
  kind: 'sign_in' | 'register';
  account?: string;
  expires_in: number;
}

export async function handoffInfo(handoff: string): Promise<HandoffInfo> {
  return (await api.get(`/auth/passkeys/handoff/${encodeURIComponent(handoff)}`)).data;
}

/** Do the handoff's passkey step on this page. */
export async function finishHandoff(handoff: string): Promise<{ kind: 'sign_in' | 'register'; account?: string; passkey?: Passkey }> {
  const start: Ceremony & { kind: 'sign_in' | 'register' } =
    (await api.post(`/auth/passkeys/handoff/${encodeURIComponent(handoff)}/options`)).data;
  const credential = start.kind === 'register' ? await create(start) : await get(start);
  return (await api.post(`/auth/passkeys/handoff/${encodeURIComponent(handoff)}/finish`,
    { ceremony: start.ceremony, credential })).data;
}
