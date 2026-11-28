import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

/** One Tailscale address (Settings › Tailscale). */
export interface TailscaleAddress {
  wanted: boolean;
  port: number;
  active: boolean;
  url: string;
  /** '' | port_in_use | same_port | needs_turning_on | needs_operator | needs_magicdns | doors_closed | slow | other
   *  | not_removed (turned off here, but Tailscale hasn't taken it down yet) */
  problem: string;
  detail: string;
  /** A page on tailscale.com the owner opens to turn the feature on. */
  needs_url: string;
}

export type TailscaleAllow = 'webhooks' | 'links' | 'app';

export interface TailscaleStatus {
  enabled: boolean;
  /** system: this computer's own Tailscale. builtin: one Cognitrix runs itself (a server). */
  mode: 'system' | 'builtin' | 'missing';
  state: 'missing' | 'stopped' | 'needs_login' | 'needs_approval' | 'starting' | 'running';
  version: string;
  name: string;
  machine: string;
  tailnet: string;
  ips: string[];
  user: string;
  tags: string[];
  key_expiry: string;
  https: boolean;
  funnel_allowed: boolean | null;
  login_url: string;
  hostname: string;
  can_sign_in: boolean;
  private: TailscaleAddress;
  public: TailscaleAddress & { allow: TailscaleAllow[]; ports: number[] };
  can_edit: boolean;
  /** How this page reached the server: direct | tailnet | public. */
  door: string;
  listen_local: boolean;
}

export interface TailscaleChange {
  enabled?: boolean;
  hostname?: string;
  private?: boolean;
  private_port?: number;
  public?: boolean;
  public_port?: number;
  allow?: TailscaleAllow[];
  listen_local?: boolean;
}

export type TailscaleCheck = Partial<Record<'private' | 'public', { ok: boolean; detail: string }>>;

export async function tailscaleStatus(): Promise<TailscaleStatus> {
  return (await api.get('/tailscale')).data;
}

export async function saveTailscale(change: TailscaleChange): Promise<TailscaleStatus> {
  return (await api.put('/tailscale', change)).data;
}

export async function connectTailscale(authKey = ''): Promise<TailscaleStatus> {
  return (await api.post('/tailscale/connect', { auth_key: authKey })).data;
}

export async function disconnectTailscale(): Promise<TailscaleStatus> {
  return (await api.post('/tailscale/disconnect')).data;
}

export async function checkTailscale(): Promise<TailscaleCheck> {
  return (await api.post('/tailscale/check')).data;
}

/** Something is waiting on the owner or on Tailscale, so the page keeps looking. */
export function tailscalePending(s: TailscaleStatus | null): boolean {
  if (!s || !s.enabled) return false;
  if (s.state === 'needs_login' || s.state === 'needs_approval' || s.state === 'starting') return true;
  if (s.private.problem === 'not_removed' || s.public.problem === 'not_removed') return true;
  return (s.private.wanted && !s.private.active) || (s.public.wanted && !s.public.active);
}

// ---- Which address to show for what ------------------------------------------------

/** GET /system/info `addresses` (cognitrix/common/addresses.py). */
export interface Addresses {
  /** The public address typed in Settings › Advanced. */
  configured: string;
  /** The Tailscale private address (tailnet only). */
  private: string;
  /** The Tailscale public address. */
  public: string;
  /** Where outside services post webhooks; '' when the page's own address is all there is. */
  hooks: string;
  /** Where outsiders open public links. */
  links: string;
  public_passes: string[];
}

let cached: Addresses | null = null;
let loading: Promise<Addresses | null> | null = null;
const watchers = new Set<(a: Addresses | null) => void>();

/** The server's addresses. `fresh` asks again and tells every open `useAddresses()`. */
export function loadAddresses(fresh = false): Promise<Addresses | null> {
  if (cached && !fresh) return Promise.resolve(cached);
  if (!loading || fresh) {
    const mine: Promise<Addresses | null> = api.get('/system/info')
      .then((r) => {
        if (loading !== mine) return cached;   // a newer ask is on its way
        cached = (r.data?.addresses as Addresses) ?? null;
        watchers.forEach((tell) => tell(cached));
        return cached;
      })
      .catch(() => null)
      .finally(() => { if (loading === mine) loading = null; });
    loading = mine;
  }
  return loading;
}

/** The server's addresses, null until loaded (or when not signed in). Follows later changes. */
export function useAddresses(): Addresses | null {
  const [addresses, setAddresses] = useState<Addresses | null>(cached);
  useEffect(() => {
    watchers.add(setAddresses);
    void loadAddresses().then((a) => { if (watchers.has(setAddresses)) setAddresses(a); });
    return () => { watchers.delete(setAddresses); };
  }, []);
  return addresses;
}

/** What of a status decides the addresses: a change means they should be asked for again. */
export function addressesKey(s: TailscaleStatus | null): string {
  if (!s) return '';
  return [s.private.active ? s.private.url : '', s.public.active ? s.public.url : '', s.public.allow.join(',')].join('|');
}

/** Where an outside service should post a webhook: the server's public address when it has one. */
export function hooksOrigin(addresses: Addresses | null, fallback: string): string {
  return (addresses?.hooks || fallback).replace(/\/$/, '');
}

/** Where someone outside opens a public link. */
export function linksOrigin(addresses: Addresses | null, fallback: string): string {
  return (addresses?.links || fallback).replace(/\/$/, '');
}
