// Cognitrix service worker: shows push notifications and opens the app when
// one is pressed. It caches nothing, so the app is never served stale.
//
// An approval arrives with Approve and Deny buttons (where the platform shows
// them) and a signed token: pressing one answers it without opening the app.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

function safeUrl(url) {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') ? url : '/inbox';
}

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { title: 'Cognitrix', body: event.data ? event.data.text() : '' };
  }
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // The open, focused app already shows its own notice.
    if (data.tag !== 'test' && windows.some((client) => client.focused)) return;
    const token = typeof data.action_token === 'string' ? data.action_token : '';
    const actions = token && Array.isArray(data.actions)
      ? data.actions
        .filter((a) => a && (a.action === 'approve' || a.action === 'deny'))
        .map((a) => ({ action: a.action, title: String(a.title || a.action) }))
      : [];
    await self.registration.showNotification(data.title || 'Cognitrix', {
      body: data.body || '',
      tag: data.tag || undefined,
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      actions,
      requireInteraction: Boolean(data.require_interaction),
      data: { url: safeUrl(data.url), token },
    });
  })());
});

async function openApp(url) {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of windows) {
    if ('focus' in client) {
      await client.focus();
      if ('navigate' in client) await client.navigate(url).catch(() => {});
      return;
    }
  }
  await self.clients.openWindow(url);
}

async function answer(data, approved) {
  try {
    const response = await fetch('/api/v1/push/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: data.token, approved }),
    });
    if (response.ok) {
      await self.registration.showNotification(approved ? 'Approved' : 'Denied', {
        body: approved ? 'The agent is carrying on.' : 'The agent was told not to.',
        tag: 'approval-answer',
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-192.png',
        silent: true,
        data: { url: data.url },
      });
      // Just a confirmation: clear it after a moment.
      await new Promise((resolve) => setTimeout(resolve, 4000));
      for (const shown of await self.registration.getNotifications({ tag: 'approval-answer' })) shown.close();
      return;
    }
  } catch (err) {
    // Offline or the server is down: open the app instead.
  }
  // Already answered, expired, or it failed: show where to answer it.
  await openApp(data.url);
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = safeUrl(data.url);
  if ((event.action === 'approve' || event.action === 'deny') && data.token) {
    event.waitUntil(answer({ ...data, url }, event.action === 'approve'));
    return;
  }
  event.waitUntil(openApp(url));
});
