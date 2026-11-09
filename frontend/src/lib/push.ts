import { api } from '@/lib/api';

// Push notifications for this browser or installed web app. The service
// worker is only registered once the person switches push on.

export interface PushDevice {
  id: string;
  label: string;
  endpoint: string;
}

export interface PushState {
  /** The server offers push (it doesn't in the desktop app). */
  available: boolean;
  public_key: string;
  subscriptions: PushDevice[];
}

export const pushSupported = () =>
  typeof window !== 'undefined'
  && 'serviceWorker' in navigator
  && 'PushManager' in window
  && typeof Notification !== 'undefined';

export async function getPushState(): Promise<PushState> {
  return (await api.get<PushState>('/push')).data;
}

function keyBytes(base64url: string): Uint8Array {
  const padded = base64url.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (base64url.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

/** "Chrome on Android" from the user agent; good enough to tell devices apart. */
export function deviceLabel(ua = navigator.userAgent): string {
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const system = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'Mac' : /Linux/.test(ua) ? 'Linux' : '';
  return system ? `${browser} on ${system}` : browser;
}

/** This browser's own subscription address, when push is on here. */
export async function currentEndpoint(): Promise<string | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager.getSubscription();
  return subscription?.endpoint ?? null;
}

export async function enablePush(publicKey: string): Promise<PushDevice> {
  if (!pushSupported()) throw new Error("This browser can't receive push notifications. On an iPhone or iPad, add Cognitrix to the Home Screen first.");
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('Notifications are blocked for this site. Allow them in the browser settings, then try again.');
  const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  await navigator.serviceWorker.ready;
  const existing = await registration.pushManager.getSubscription();
  // A subscription made for another server key can't be reused.
  if (existing) await existing.unsubscribe();
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes(publicKey) as BufferSource,
  });
  const json = subscription.toJSON();
  const { data } = await api.post<PushDevice>('/push/subscriptions', {
    endpoint: json.endpoint, keys: json.keys, label: deviceLabel(),
  });
  return data;
}

export async function disablePush(): Promise<void> {
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return;
  await api.post('/push/unsubscribe', { endpoint: subscription.endpoint }).catch(() => {});
  await subscription.unsubscribe();
}

export async function removePushDevice(id: string): Promise<void> {
  await api.delete(`/push/subscriptions/${id}`);
}

export async function sendTestPush(): Promise<void> {
  await api.post('/push/test');
}
