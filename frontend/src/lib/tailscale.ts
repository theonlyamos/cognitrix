import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

/** One Tailscale address (Settings › Tailscale). */
export interface TailscaleAddress {
  wanted: boolean;
  port: number;
  active: boolean;
  url: string;
  /** '' | port_in_use | same_port | needs_turning_on | needs_operator | needs_magicdns | doors_closed | slow | other */
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

export function loadAddresses(fresh = false): Promise<Addresses | null> {
  if (cached && !fresh) return Promise.resolve(cached);
  if (!loading || fresh) {
    loading = api.get('/system/info')
      .then((r) => { cached = (r.data?.addresses as Addresses) ?? null; return cached; })
      .catch(() => null)
      .finally(() => { loading = null; });
  }
  return loading;
}

/** The server's addresses, null until loaded (or when not signed in). */
export function useAddresses(): Addresses | null {
  const [addresses, setAddresses] = useState<Addresses | null>(cached);
  useEffect(() => {
    let alive = true;
    void loadAddresses().then((a) => { if (alive) setAddresses(a); });
    return () => { alive = false; };
  }, []);
  return addresses;
}

/** Where an outside service should post a webhook: the server's public address when it has one. */
export function hooksOrigin(addresses: Addresses | null, fallback: string): string {
  return (addresses?.hooks || fallback).replace(/\/$/, '');
}

/** Where someone outside opens a public link. */
export function linksOrigin(addresses: Addresses | null, fallback: string): string {
  return (addresses?.links || fallback).replace(/\/$/, '');
}
