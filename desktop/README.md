# Cognitrix desktop app

A Tauri 2 shell around the existing React UI. The Python engine (FastAPI API +
the task worker) is frozen with PyInstaller into `src-tauri/engine/` and
bundled as a resource; the app starts it on `127.0.0.1:48210` (or a free port)
and loads the UI the engine serves.

```
desktop/
  splash/                 start screen shown while the engine boots or if it fails
  scripts/build_engine.py builds frontend/dist, then the engine with PyInstaller
  scripts/smoke_engine.py starts the built engine and checks it serves the UI
  cognitrix-engine.spec   PyInstaller spec (one folder, not one file)
  src-tauri/              Rust: engine lifecycle, tray, Alt+Space quick ask,
                          notifications, start at login, desktop settings
```

## Build the installers

Needs Python 3.11–3.13, Node 22 with pnpm, and Rust (stable). Build on the OS
you're targeting: PyInstaller can't cross-compile the engine.

```
pip install . pyinstaller
python desktop/scripts/build_engine.py
python desktop/scripts/smoke_engine.py   # optional check
cd desktop
npm ci
npx tauri build --bundles nsis,msi          # Windows -> bundle/{nsis,msi}/
npx tauri build --bundles deb,rpm,appimage  # Linux   -> bundle/{deb,rpm,appimage}/
npx tauri build --bundles app,dmg           # macOS   -> bundle/{macos,dmg}/
```

Linux also needs the WebKitGTK build libraries:
`sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev patchelf libxdo-dev`.

The `desktop` GitHub Actions workflow builds all three (Windows, Ubuntu 22.04,
Apple-silicon macOS). Each run uploads `cognitrix-windows`, `cognitrix-linux`
and `cognitrix-macos` artifacts, and a published release gets the installers
attached. It runs on PRs that touch the desktop code, on releases, and from
the Actions tab.

The builds aren't code-signed yet. Windows SmartScreen asks for "Run anyway".
macOS says the app "is damaged" when it's downloaded unsigned: run
`xattr -cr /Applications/Cognitrix.app` once after installing.

## Develop

- UI only: run `cognitrix --web` (or `python -m cognitrix.desktop engine --port 8000`)
  and `pnpm dev` in `frontend/` as usual. Desktop-only features stay off in a browser.
- Shell against a Python checkout: `COGNITRIX_ENGINE_CMD="python -m cognitrix.desktop" npx tauri dev`
- Shell against an engine that's already running: `COGNITRIX_ENGINE_URL=http://127.0.0.1:8000 npx tauri dev`

## Where things live

On Windows (on Linux and macOS, the same folders sit under your home folder and the app's config/data folders):

- Engine data (database, task queue, `.env` with provider keys): `%USERPROFILE%\.cognitrix`
  (shared with the `cognitrix` CLI)
- Agents' working folder (shell + file tools are confined to it): `%USERPROFILE%\Cognitrix` by default, changeable in Settings
- Engine log: `%LOCALAPPDATA%\com.theonlyamos.cognitrix\logs\engine.log`
- Desktop preferences: `%APPDATA%\com.theonlyamos.cognitrix\desktop.json`

## Google and GitHub sign-in

The sign-in and sign-up pages show "Continue with Google/GitHub" once the
provider's OAuth app is set up. Put the credentials in `~/.cognitrix/.env`
(desktop) or the server environment (web), then restart the engine:

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
```

Register this redirect / callback URL with each provider:

- Desktop app: `http://127.0.0.1:48210/api/v1/auth/oauth/google/callback`
  (and `.../github/callback`). Google: create a "Web application" OAuth client.
- Web server: `https://<your-host>/api/v1/auth/oauth/<provider>/callback`. Set
  `COGNITRIX_PUBLIC_URL=https://<your-host>` when the server sits behind a proxy.

In the desktop app, sign-in opens in your browser and the app picks up the
result when you finish. A verified email that matches an existing account
signs into it. A new email creates an account only when signup is open (the
first account, or `COGNITRIX_ALLOW_SIGNUP=true`).
