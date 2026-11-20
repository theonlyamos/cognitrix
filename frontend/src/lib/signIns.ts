import { api } from '@/lib/api';

/** One device signed in to this account (Settings › Sign-ins). */
export interface SignInDevice {
  id: string;
  name: string;
  kind: 'browser' | 'desktop' | 'cli';
  /** How it signed in: 'password' | 'google' | 'github' | 'code' | 'earlier'. */
  method: string;
  /** For a code: the device that made it. */
  approved_by: string;
  address: string;
  created_at: string;
  last_seen_at: string;
  current: boolean;
  ended?: boolean;
}

export interface SignInList {
  sign_ins: SignInDevice[];
  /** False when this device signed in before per-device sign-ins (it isn't in the list yet). */
  current_listed: boolean;
  code_sign_in: boolean;
}

export interface SignInCode {
  code: string;
  expires_in: number;
}

export type SignInCodeStatus =
  | { status: 'waiting'; expires_in: number }
  | { status: 'used'; sign_in?: SignInDevice }
  | { status: 'expired' };

export async function listSignIns(): Promise<SignInList> {
  return (await api.get('/auth/sign-ins')).data;
}

export async function endSignIn(id: string): Promise<{ was_current: boolean }> {
  return (await api.delete(`/auth/sign-ins/${encodeURIComponent(id)}`)).data;
}

export async function signOutOthers(): Promise<number> {
  return (await api.post('/auth/sign-ins/sign-out-others')).data.signed_out;
}

/** Sign this device out on the server too (best effort; the caller clears the token either way). */
export async function signOutHere(): Promise<void> {
  await api.post('/auth/sign-ins/sign-out');
}

export async function makeSignInCode(): Promise<SignInCode> {
  return (await api.post('/auth/sign-ins/codes')).data;
}

export async function signInCodeStatus(code: string): Promise<SignInCodeStatus> {
  return (await api.get(`/auth/sign-ins/codes/${encodeURIComponent(code)}`)).data;
}

export async function cancelSignInCode(code: string): Promise<void> {
  await api.delete(`/auth/sign-ins/codes/${encodeURIComponent(code)}`);
}

/** The link a phone camera opens to sign in with the code (it stays in the #, so it never reaches a server log). */
export function signInLink(code: string): string {
  return `${window.location.origin}/#signin-code=${code.replace(/[^A-Za-z0-9]/g, '')}`;
}

/** A code in this page's address (from a scanned QR code), or ''. */
export function codeFromAddress(): string {
  const m = /signin-code=([A-Za-z0-9]{8})/.exec(window.location.hash || '');
  return m ? `${m[1].slice(0, 4)}-${m[1].slice(4)}`.toUpperCase() : '';
}
