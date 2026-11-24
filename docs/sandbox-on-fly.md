# The agents' computer on Fly

When Cognitrix runs on Fly, each person's sandboxed computer (the Linux
desktop agents use, with one desktop per agent) can run as its own
[Fly Machine](https://fly.io/docs/machines/) instead of a local Docker
container. Machines stop when nobody has used them for a while, so an idle
computer costs only its volume, and start again on the next request.

How it works (code: `cognitrix/computer/sandbox/fly.py`):

- **One machine per person**, in a separate Fly app, made the first time an
  agent needs the computer. Its home folder (files, downloads, browser
  logins, coding folders, Apps) is a Fly volume, so it survives stops,
  updates and restarts. Reset in the Computer panel deletes both.
- **No public address.** The machine has no services, so it can't be reached
  from the internet. The engine talks to it over Fly's private network
  (6PN), and every call still needs the machine's own random token.
- **Sleeps when idle.** Every minute the engine asks each running machine how
  long since it was last used. After the idle time set in Settings
  (default 15) with no request, no command running, nobody in control and
  nobody watching its screen, the engine stops it (or suspends it).
- **Wakes on the next request**: an agent using a computer tool, Run in
  sandbox, an App's page, or the Wake button in the Computer panel. Waking
  from stopped takes a few seconds plus the desktop start; open windows are
  gone, files and logins are not. With **When idle** set to suspend
  the machine's memory is kept and windows come back, where Fly allows it.

## One-time setup

The engine (the `cognitrix` app in `fly.toml`) must run on Fly, in the same
Fly organisation as the sandbox app, so it can reach the private network.

1. **Make the image public.** The engine starts machines from
   `ghcr.io/theonlyamos/cognitrix-desktop` (built by
   `.github/workflows/sandbox-image.yml`). In GitHub, open the
   `cognitrix-desktop` package settings and set it to Public, so Fly can pull
   it. The image must include this change (it adds `cognitrix-sandbox-root`
   and the `/activity` check), so publish a new image first.

2. **Create the app the machines live in** (no deploy, no fly.toml):

   ```sh
   fly apps create cognitrix-sandboxes --org <your-org>
   ```

3. **Make a token that can only manage that app**:

   ```sh
   fly tokens create deploy -a cognitrix-sandboxes --expiry 8760h
   ```

   Then, signed in as an admin, open **Settings > Computers**, set **Where
   agents get a computer** to Fly.io, and fill in **Cloud computers on
   Fly.io**: the token, the app name (`cognitrix-sandboxes`) and, if you want
   another region than the engine's own (`FLY_REGION`, set by Fly), a region.

4. Optional settings on the same page:

   | Setting | Default | What it does |
   | --- | --- | --- |
   | Stop an idle computer after | `15` minutes | `0` never stops |
   | When idle | Stop it | Suspend keeps memory and open windows (Fly only suspends some machine sizes; others are stopped) |
   | CPUs / CPU kind | `2` / shared | Machine size |
   | Memory (MB) | `4096` | Memory |
   | Disk (GB) | `10` | Home folder size (set when a person's computer is first made) |
   | Computer image | `ghcr.io/theonlyamos/cognitrix-desktop:latest` | Pin a version, e.g. `:v0.5.6`; stopped machines move to it when they next wake |

5. Restart the engine. Settings, Computer then shows
   "on Fly", or which setting is still missing.

## Updating the computers

Fly looks up the image tag again each time a machine's config is updated.
Two ways to roll out a new image:

- Pin the version in **Computer image** (Settings > Computers). Running computers show the
  Update button in the Computer panel, and stopped ones move to the new image
  when they wake.
- With `:latest`, Reset (or deleting a machine in the Fly dashboard) makes a
  fresh machine on the newest image; the volume is kept unless you Reset.

## Hidden keys and snapshots

Both work on Fly too. There's no `docker exec` into a Fly Machine, so the
engine runs the few scripts they need (unpacking or packing the home folder,
installing the key proxy's certificate) through the machine's own API, as
its unprivileged `agent` user. Computers on an image older than this change
say "update it" instead; press Update in the Computer panel.

- **Hidden keys.** The key proxy runs inside the engine and listens on
  `::` (all IPv6 addresses), so machines reach it over the private network
  at the engine's `FLY_PRIVATE_IP` (set by Fly). Each request still needs
  that person's own proxy password, which is also why the proxy never
  answers the public internet: the `cognitrix` app's `fly.toml` doesn't
  expose its port. Set `COGNITRIX_SANDBOX_PROXY_BIND` / `_HOST` to change
  where it listens or the address the computers use.
- **Snapshots** are kept on the engine (its data volume), like on Docker.
  Restoring the whole computer closes its desktops, empties the home folder,
  unpacks the snapshot, then stops the machine; it wakes on the next request
  with the restored files.

## Cost

Fly bills machines per second while they run, and volumes per GB-month. With
the defaults (2 shared CPUs, 4 GB) a computer costs roughly $25/month if it
never sleeps; with the 15-minute idle stop a typical person's computer runs a
few hours a day, so a few dollars a month, plus about $1.50/month for a 10 GB
volume. Check Fly's current prices before budgeting.

## Security notes

- Machines can reach the internet, and other apps on your organisation's
  private network. Give internal services (Redis, databases) passwords, and
  don't rely on the private network alone. A separate Fly organisation for
  the sandbox app isolates it fully, but the engine then can't reach it, so
  that isn't supported yet.
- Each person's machine has its own token; one person's computer can't drive
  another's even on the shared network. The live screen also needs a
  per-desktop VNC password.
- The machine starts as root only to hand its home folder to the `agent`
  user (Fly volumes mount owned by root), then drops all privileges
  (`sandbox/desktop/bin/cognitrix-sandbox-root`).
