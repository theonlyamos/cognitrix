# Passkeys

Sign in with Windows Hello, Touch ID, a phone or a security key instead of a password.

## Using them

- **Add one:** Settings › Sign-ins › Passkeys › **Add a passkey**. Your browser asks you to make one. You can add several (one per device, or one synced through a password manager), rename them and remove them there.
- **Sign in:** on the sign-in page, press **Sign in with a passkey**. You don't type your email: the passkey says whose it is. Browsers that support passkey autofill also offer your passkey when you click the email field.
- Each passkey sign-in shows up in Settings › Sign-ins as "Signed in with a passkey (name)", and can be signed out on its own like any other.
- Adding a passkey sends you a notice and goes in the activity log.

## Where they work

A passkey belongs to the address it was made on (`cognitrix.example.com`, `localhost`) and only works there.

- **Web:** needs `https`, or `http://localhost`. Browsers never allow passkeys on an IP address, so a server opened as `http://127.0.0.1:8000` shows a link to open the same page at `http://localhost:8000` instead.
- **Desktop app (Windows, macOS, Linux):** the app's window shows the engine's pages on `http://127.0.0.1`, which can't use passkeys. On macOS the system webview also only allows passkeys for apps with Apple's associated-domains setup, and the Linux webview (WebKitGTK) has no passkey support at all. So the app does the passkey step in your **default browser**: it opens `http://localhost:<port>/passkey?handoff=…`, you use your passkey there, and the app signs in (or saves the new passkey) by itself. The browser tab says when you can close it.
  - Passkeys made this way belong to `localhost`, so they keep working when the app's port changes.
  - Only the desktop engine offers this, and only to callers on the same computer. The app keeps a secret for each step; the browser page only has a one-time link that expires in 5 minutes.

## For admins

- Turn passkeys on or off for everyone: Settings › People › Sign-up and sign-in › **Sign in with a passkey** (on by default).
- Only public keys are stored (the `Passkey` table). Each prompt's challenge works once, within 5 minutes. Prompts are limited to 30 per address per 10 minutes.
- Behind a reverse proxy, the page's address must match the `Host` (or `X-Forwarded-Host`) the server sees, the public address in Settings › Advanced, or an allowed CORS origin.
