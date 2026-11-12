# The agents' sandboxed computer

Agents get their own computer: a Linux desktop in a Docker container, driven
with the same driver as background computer use (`cognitrix/computer`). People
watch it live and take over when a step needs them (sign-ins, 2FA, payments).

## Shape

- **One container per user**, started on first use and kept with a home
  volume (`cognitrix-sandbox-home-<user>`), so files and logins survive.
  Reset throws both away.
- **One desktop per agent inside it**, like virtual desktops: each is its own X
  display with its own window manager, apps, mouse and keyboard; files are
  shared. Each desktop gets its own browser profiles, because one profile
  can't be open on two desktops at once.
- **The driver runs inside the container**, one worker process per desktop
  (`sandbox/worker.py`), behind a supervisor (`sandbox/supervisor.py`) that
  starts desktops and forwards calls. The engine uses `RemoteDriver`
  (`sandbox/client.py`), which has the same calls as `Driver`, so the window
  tools work unchanged.
- **Only `cognitrix/computer` ships in the image.** Provider keys, the
  database and the person's token never enter the container; the container
  token never reaches the desktop's apps.

## Turning it on

Settings › Agent access › Agents' computer (admins), or
`COGNITRIX_COMPUTER_SANDBOX=docker`, which wins. The image is
`ghcr.io/theonlyamos/cognitrix-desktop:latest` (published by
`.github/workflows/sandbox-image.yml`), pulled on first use; set
`COGNITRIX_SANDBOX_IMAGE` to use another one. Build locally with
`docker build -f sandbox/desktop/Dockerfile -t cognitrix-desktop:latest .`

## Driver behaviour in a sandbox

`Driver(backend, sandbox=True)`:

- Typing and key presses use real input (X Test) right away, because
  Chromium and Electron ignore background input on Linux and nobody else uses
  this screen. A field that won't take text in the background is filled by
  clicking it, selecting all and typing.
- `hold(by)` / `release()`: while a person has control, the agent's actions
  and looks wait (up to `hold_wait_s`) and then carry on, so the two never
  fight over the mouse and the model never sees what the person typed.

Accessibility must be on for browsers to expose pages: the image sets the
AT-SPI `IsEnabled` flag, `ACCESSIBILITY_ENABLED=1` and
`--force-renderer-accessibility` for Chromium.

## Watching and taking over

The Computer panel beside the chat shows each agent's desktop through noVNC.
The browser gets a one-time, one-minute ticket for the screen websocket (the
engine proxies it to the container, whose ports listen on 127.0.0.1 only).
Each desktop has two VNC passwords: the view-only one comes with the ticket;
the one that accepts mouse and keyboard only comes back from Take over.

Agents can ask with `ask_to_take_over(reason)`: it is always asked as an
approval, and returns once the person answers (after handing back).

## Approvals

With the sandbox on, the window tools (`open_app`, `click_element`,
`type_in_window`, ...) are low risk and don't ask: they act on the agent's own
computer. The full-screen tools (`mouse_click`, `take_screenshot`, ...) still
act on the engine's machine, so they refuse and point at the window tools.

## Not done yet

- Hosting on Fly (one machine per user, idle shutdown) for the web app.
- Network rules inside the container (block the cloud metadata address and
  private ranges) for shared servers.
- Step captions and the agent's pointer in the panel (the `ActionEvent` feed
  already carries them, marked with `desktop`).
- Idle shutdown of desktops and containers.
- Reading a Chromium text field's value back after typing (it shows as
  "unverified").
