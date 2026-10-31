# Cognitrix desktop app

A Tauri 2 shell around the existing React UI. The Python engine (FastAPI API +
the task worker) is frozen with PyInstaller into `src-tauri/engine/` and
bundled as a resource; the app starts it on `127.0.0.1:48210` (or a free port)
and loads the UI the engine serves.

```
desktop/
  splash/                 start screen shown while the engine boots or if it fails
  scripts/build_engine.py builds frontend/dist, then the engine with PyInstaller
  cognitrix-engine.spec   PyInstaller spec (one folder, not one file)
  src-tauri/              Rust: engine lifecycle, tray, Alt+Space quick ask,
                          notifications, start with Windows, desktop settings
```

## Build the Windows installer

Needs Python 3.11–3.13, Node 22 with pnpm, and Rust (stable, MSVC).

```
pip install . pyinstaller
python desktop/scripts/build_engine.py
cd desktop
npm ci
npx tauri build      # -> src-tauri/target/release/bundle/{nsis,msi}/
```

The `desktop` GitHub Actions workflow does the same on `windows-latest` and
uploads the installers as the `cognitrix-windows` artifact (run it from the
Actions tab, or it runs on PRs that touch the desktop code).

## Develop

- UI only: run `cognitrix --web` (or `python -m cognitrix.desktop engine --port 8000`)
  and `pnpm dev` in `frontend/` as usual. Desktop-only features stay off in a browser.
- Shell against a Python checkout: `COGNITRIX_ENGINE_CMD="python -m cognitrix.desktop" npx tauri dev`
- Shell against an engine that's already running: `COGNITRIX_ENGINE_URL=http://127.0.0.1:8000 npx tauri dev`

## Where things live on Windows

- Engine data (database, task queue, `.env` with provider keys): `%USERPROFILE%\.cognitrix`
  (shared with the `cognitrix` CLI)
- Agents' working folder (shell + file tools are confined to it): `%USERPROFILE%\Cognitrix` by default, changeable in Settings
- Engine log: `%LOCALAPPDATA%\com.theonlyamos.cognitrix\logs\engine.log`
- Desktop preferences: `%APPDATA%\com.theonlyamos.cognitrix\desktop.json`
