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
  long since it was last used. After `COGNITRIX_SANDBOX_IDLE_MINUTES`
  (default 15) with no request, no command running, nobody in control and
  nobody watching its screen, the engine stops it (or suspends it).
- **Wakes on the next request**: an agent using a computer tool, Run in
  sandbox, an App's page, or the Wake button in the Computer panel. Waking
  from stopped takes a few seconds plus the desktop start; open windows are
  gone, files and logins are not. With `COGNITRIX_SANDBOX_FLY_IDLE=suspend`
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

3. **Make a token that can only manage that app**, and give it to the engine
   with the other settings:

   ```sh
   fly tokens create deploy -a cognitrix-sandboxes --expiry 8760h
   fly secrets set -a cognitrix \
     COGNITRIX_COMPUTER_SANDBOX=fly \
     COGNITRIX_SANDBOX_FLY_APP=cognitrix-sandboxes \
     FLY_API_TOKEN='<the token from the line above>'
   ```

   The region defaults to the engine's own (`FLY_REGION`, set by Fly). Set
   `COGNITRIX_SANDBOX_FLY_REGION` to choose another.

4. Optional settings (as secrets or in `[env]` of `fly.toml`):

   | Setting | Default | What it does |
   | --- | --- | --- |
   | `COGNITRIX_SANDBOX_IDLE_MINUTES` | `15` | Stop after this many idle minutes; `0` never stops |
   | `COGNITRIX_SANDBOX_FLY_IDLE` | `stop` | `suspend` keeps memory and open windows (Fly only suspends some machine sizes; others are stopped) |
   | `COGNITRIX_SANDBOX_FLY_CPUS` / `_CPU_KIND` | `2` / `shared` | Machine size |
   | `COGNITRIX_SANDBOX_FLY_MEMORY_MB` | `4096` | Memory |
   | `COGNITRIX_SANDBOX_FLY_VOLUME_GB` | `10` | Home folder size (set when a person's computer is first made) |
   | `COGNITRIX_SANDBOX_IMAGE` | `ghcr.io/theonlyamos/cognitrix-desktop:latest` | Pin a version, e.g. `:v0.5.6`; stopped machines move to it when they next wake |

5. Redeploy or restart the engine. Settings, Computer then shows
   "on Fly", or which setting is still missing.

## Updating the computers

Fly looks up the image tag again each time a machine's config is updated.
Two ways to roll out a new image:

- Pin the version in `COGNITRIX_SANDBOX_IMAGE`. Running computers show the
  Update button in the Computer panel, and stopped ones move to the new image
  when they wake.
- With `:latest`, Reset (or deleting a machine in the Fly dashboard) makes a
  fresh machine on the newest image; the volume is kept unless you Reset.

## Not on Fly yet

Hidden keys (the key proxy) and computer snapshots need local Docker, so a
computer hosted on Fly says they aren't available. To pass keys into Fly
computers directly, set `COGNITRIX_SANDBOX_HIDE_KEYS=0` on the engine.

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
