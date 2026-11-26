# Cognitrix

Cognitrix is an open-source app for building AI agents that do real work for you. Each agent has its own model, tools, memory and a sandboxed Linux computer it can use like a person would. You talk to agents in chat, hand them tasks that run in the background, put them in teams, and connect them to your apps, inbox, phone and code editor.

It runs as a **desktop app** (Windows, macOS, Linux), a **self-hosted web app**, or from the **command line**, all on the same Python engine.

## Contents

- [What it can do](#what-it-can-do)
- [Install](#install)
- [Command line](#command-line)
- [Configuration](#configuration)
- [Self-hosting](#self-hosting-with-docker)
- [API access](#api-access)
- [Development](#development)

## What it can do

### Agents and chat

- **Any model you have access to.** OpenAI, Anthropic, Google Gemini, OpenRouter, Groq, Cerebras and local models through Ollama, plus a ChatGPT Plus/Pro plan sign-in. Pick a reasoning level per chat where the model supports it.
- **Coding agent CLIs as models.** Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, OpenCode and Antigravity CLI can drive an agent, using the CLI's own sign-in.
- **Rich chat.** Attach files and images, steer a reply while it is running, ask side questions, and leave a chat running in the background. Chats can be grouped, searched, shared by link, and show how long each reply took and how many tokens it used.
- **Voice.** Dictate messages, hear replies read aloud, or hold a live voice conversation.
- **Artifacts.** Agents publish web pages, dashboards, spreadsheets and whiteboards that open beside the chat, take comments, and can be shared with a public link.
- **Apps.** Agents can build and host a small live web app with its own data, which you open from a stable link.
- **Image generation** with Gemini or OpenAI.
- **AGENTS.md.** Instructions in `AGENTS.md` files (including nested ones) in a working folder are picked up in every chat, on your PC and in the sandbox.

### Computers

- **A sandboxed computer for every agent.** When Docker is installed, agents get their own Linux desktop (with Chromium, Firefox and common dev tools) in a container, one container per user and one desktop per agent. You can watch it live and take over from the Computer panel. The image is `ghcr.io/theonlyamos/cognitrix-desktop` and updates itself in the background. It can also run on [Fly.io](docs/sandbox-on-fly.md).
- **Computer and browser use.** Agents click, type and read the screen through accessibility APIs, on their sandbox or (with permission) in background windows on your own PC.
- **Code in the sandbox.** Coding work runs in the sandbox by default, with your project copied in, and the coding agent CLIs are installed there.
- **Teach a task.** Show a task once on the sandbox desktop and Cognitrix turns the recording into a reusable skill.
- **Pair your own PC.** Let agents on a Cognitrix web server work in chosen folders, run commands and use apps on your computer with `cognitrix connect`. See [docs/pair-your-pc.md](docs/pair-your-pc.md).
- **Working folders and a shell tool**, with per-folder read-only or read-write access.

### Tasks, automations and teams

- **Tasks** are planned, multi-step jobs that run in the background with their own run history, events, budgets and approvals. They can run once, on an interval, on a cron schedule, or when mail arrives, and report their results back to the agent that created them.
- **Always-on agents** keep notes between runs, schedule their own next check, react to webhooks and notify you when something needs you.
- **A proactive assistant** checks in during the day and sends a morning brief, with quiet hours you control.
- **Teams and group chats.** Put agents in a team and talk to all of them at once. `@name` asks one agent, `@all` asks everyone, and agents hand work to each other by mentioning a teammate.
- **Swarms.** A lead agent splits a big job across many worker agents running in parallel and gets one report back.
- **Pull request watching.** Agents that open or are asked to watch a GitHub pull request wake up when its checks, reviews or comments change.
- **Projects** group chats, tasks, files and pull requests around one goal, with a Changes tab for what agents changed.
- **Dashboard and Inbox** show what is running, what finished and what is waiting for your approval or answer.

### Memory and knowledge

- **Global memory** that every agent shares, with an "About you" summary you can read and edit. You approve what gets remembered, can import memories from other assistants, and export everything.
- **Dream mode** tidies and summarises memories every night (or on demand with **Tidy now**), and a nightly review of finished chats and task runs turns what worked into facts and reusable **skills**.
- **Notion backup** keeps a copy of your memories in your own Notion after every tidy, with a one-click restore.
- **Knowledge.** Add files and web pages to a knowledge base that agents search, and see where each source was used.
- **Skills** in the `SKILL.md` format, shared across the server or personal to you, runnable as slash commands.

### Connections

- **Connected apps** with one-click sign-in: GitHub, Gmail, Google Calendar, Drive, Docs, Sheets and Slides, Slack, Notion, Linear and Sentry. Their tools load into agents automatically.
- **Any MCP server**, added per user.
- **Chat apps.** Talk to your agents from Telegram, Slack, Discord, Microsoft Teams, WhatsApp or SMS (Twilio).
- **Agent email.** Give each agent its own address on a mailbox you connect, and it answers mail as a chat.
- **Meetings.** Agents can join Google Meet, Microsoft Teams and Zoom calls from their sandbox browser and take notes.
- **Payments.** Agents can ask to pay at a checkout; every payment needs your OK and stays within limits you set.
- **Code editors.** `cognitrix acp` lets Zed, JetBrains IDEs and other [Agent Client Protocol](https://agentclientprotocol.com) editors chat with your agents.
- **An HTTP API** with scoped keys and an OpenAI-compatible endpoint. See [API access](#api-access).

### Safety and control

- **Approvals.** Risky tool calls (deleting, overwriting, sending, shell commands) ask first, with **Always allow** for the ones you trust. Each agent, and each tool on it, can be set to always allow, always ask, ask only when the agent works on its own, hand the action to you to do, or never run.
- **Second-model review.** A second model can screen risky actions and allow, block or hand them to you. See [Configuration](#configuration).
- **A credential vault.** Saved sign-ins and keys are encrypted at rest, and agents use them by name without ever seeing the values.
- **Multi-user servers** with invitations, roles, spending limits, an audit log and per-device sign-ins (including signing in by code or QR from another device).

### The apps

- **Desktop app** (Tauri) for Windows, macOS (Apple silicon and Intel) and Linux, with a tray icon, notifications, a global **Alt+Space** quick-ask window, launch at login and self-update.
- **Web app** that works on phones as well as desktops, in light, dark and custom themes (agents can make themes too), in English, Spanish and French.

## Install

### Desktop app

Download the installer for your system from the [latest release](https://github.com/theonlyamos/cognitrix/releases/latest):

| System | File |
| --- | --- |
| Windows | `.exe` (setup) or `.msi` |
| macOS | `.dmg` (Apple silicon or Intel) |
| Linux | `.deb`, `.rpm` or `.AppImage` |

The app includes everything it needs. On first start it asks you to create an account and set up your assistant with a model and API key.

Builds may be unsigned. On Windows, SmartScreen then asks you to choose "Run anyway". On macOS, run `xattr -cr /Applications/Cognitrix.app` once after installing if it says the app "is damaged".

To give agents their own sandboxed computer, install [Docker](https://docs.docker.com/get-docker/). Cognitrix pulls the sandbox image the first time an agent needs it.

### Python package (command line and API)

Needs **Python 3.11 to 3.13**.

```bash
pip install cognitrix
cognitrix --help
```

The PyPI package has the engine, the command line, the API, `cognitrix acp` and `cognitrix connect`. It does **not** include the built web UI; for that, use the desktop app, Docker, or a source install.

### From source (with the web UI)

Needs Python 3.11 to 3.13 and Node.js 22 with [pnpm](https://pnpm.io).

```bash
git clone https://github.com/theonlyamos/cognitrix.git
cd cognitrix

cd frontend
pnpm install
pnpm run build
cd ..

pip install -e .
cognitrix --ui web
```

Open `http://localhost:8000` and create an account; the first account is the owner and admin.

Use an editable install (`pip install -e .`): the server looks for the built UI in `frontend/dist` next to the package, which a regular install would not find. To serve a UI built somewhere else, set `COGNITRIX_FRONTEND_DIR` to its `dist` folder.

## Command line

```bash
cognitrix                              # chat with the Assistant agent in the terminal
cognitrix --ui tui                     # full-screen terminal UI
cognitrix --ui web                     # web app on http://localhost:8000
cognitrix --agent "Researcher"         # chat with a saved agent
cognitrix --provider anthropic --model claude-sonnet-5-5
cognitrix -p "Summarise README.md"     # one prompt, print the reply, exit
cognitrix -p "Plan a launch" --task    # run the prompt as a planned multi-step task
cognitrix -c -p "And the next step?"   # continue the previous session
```

| Command | What it does |
| --- | --- |
| `cognitrix agents -l` / `--new` / `<name>` | List, create, show, update or delete agents |
| `cognitrix tools -l [category]` | List tools (`general`, `system`, `web`) |
| `cognitrix skills -l` / `--install` / `--run` | Manage and run skills |
| `cognitrix acp --agent Assistant` | Run as an Agent Client Protocol server for code editors |
| `cognitrix connect <server>` | Pair this PC with a Cognitrix web server |
| `cognitrix reset-password <email>` | Set a new password for a local account |
| `cognitrix --sessions` / `--session <id>` | List or reopen saved chats |

Run `cognitrix --help` or `cognitrix <command> --help` for every option.

### Fully autonomous runs

For benchmarks and throwaway containers, `--dangerously-skip-permissions` (also `--yolo` or
`--dangerously-bypass-approvals-and-sandbox`) runs with no approval prompts at all:

```bash
cognitrix --yolo -p "Fix the failing tests in this repo"
```

Every approval is answered Yes for that run only: tool calls, full-shell commands, coding agents, copy back
from the sandbox, Request Access, sub-agents and task runs it starts (even ones a worker picks up). The bash
tool runs any command in a real shell. A request for you to take over the agent's computer is refused at once,
since nobody is watching. Tools you set to "never" stay blocked, and your saved settings don't change. It
prints a warning on stderr, so a `-p` answer on stdout stays clean. It is ignored for `--ui web`.

### Code editors (Agent Client Protocol)

In Zed, add this to `settings.json`:

```json
{
  "agent_servers": {
    "Cognitrix": {
      "command": "cognitrix",
      "args": ["acp", "--agent", "Assistant"]
    }
  }
}
```

The chat works in the editor's project folder, tool calls show up in the editor, and approvals ask there. Your agents appear in the editor's mode menu. With only the desktop app installed, point `command` at its engine instead: `cognitrix-engine` with `"args": ["acp"]` (on Windows it is in the app's install folder under `engine\`). Add `--user you@example.com` to use one account's agents and sign-ins when several people share the machine.

## Configuration

Everything is set in the app under **Settings**: model providers and API keys, web search keys, email sending, sign-in with Google or GitHub, the GitHub App, chat apps, the agents' computers (Docker or Fly.io), push alerts, safety, sharing, and limits and timeouts (**Settings > Advanced**). Only an admin can change the app-wide ones. Cognitrix does not read these from environment variables or `.env`, so whoever downloads it can set it up without touching a config file.

The server's own setup is in the app too. On a new server, the first page asks where to keep data (the built-in SQLite, or PostgreSQL, MySQL or MongoDB) and how to queue task runs (the built-in queue, or Redis). Most people keep the built-in choices and continue. Both can be changed later in **Settings > Advanced > Database and job queue**, along with the addresses allowed to call the API and the content security policy. The keys that sign sign-ins and encrypt saved secrets are made by the server on first start and kept in the data folder (`~/.cognitrix`), so there is nothing to generate or set.

On a machine without a browser at hand, the same settings work from the command line:

```bash
cognitrix settings list --page advanced
cognitrix settings set CELERY_BROKER_URL=redis://localhost:6379/0
cognitrix settings set DB_TYPE=sqlite    # back to the built-in database if a change keeps the server from starting
```

Upgrading a server that used to set `JWT_SECRET_KEY` or `COGNITRIX_SECRETS_KEY` as environment variables: on its first start the server copies them into the data folder, so sign-ins and saved secrets keep working. After that the variables are not read and can be removed.

### Second-model review of risky actions

Tool calls that would normally ask for approval can be screened by a second model first. It answers **allow** (runs without asking), **block** (refused, and the agent is told why) or **ask** (you get the usual approval prompt). Errors, timeouts and unclear answers count as **ask**.

Turn it on in **Settings > Advanced > Safety** (off, when nobody is watching, or always), and pick the reviewer's provider and model there. Agents can override it.

The reviewer never answers for tools you set to "Ask first" or for commands that need the full shell. Task runs record each verdict as an `action_reviewed` run event.

### Run Code and tools on demand

Both are on by default for every agent, and can be turned off on an agent's **Tools** tab:

- **Run Code** lets the agent write one short JavaScript snippet that calls many tools (`await tools.grep({pattern: "TODO"})`, loops, `Promise.all`) and returns only what it needs, instead of one model step per call. Scripts run in a V8 isolate with no file system or network of their own, and each tool call inside goes through the same permissions and approvals as a direct call. A time limit (default 600 seconds) and a call limit (default 200) per script are in **Settings > Advanced**.
- **Load tools only when needed** sends the model full descriptions of a few core tools and just the names of the rest, which it loads with **Find Tools** when it needs them.

## Self-hosting with Docker

`docker-compose.yml` runs three services: the **web** app (FastAPI plus the in-process scheduler), a **worker** (Celery, which runs tasks and team runs), and **Redis** as the broker. Web and worker share the SQLite database over a named volume.

```bash
docker compose up --build
```

The app is then at `http://localhost:8000`. Nothing needs to be set beforehand: compose points the app at its Redis container, and everything else is set in the app.

- Scale execution with `docker compose up --scale worker=3`. Keep **web at one replica**: the schedule loop runs per process, so several web containers would fire the same schedules more than once.
- State lives in the `cognitrix-data` volume (settings, database, keys, MCP config) and `redis-data`. For heavier task concurrency, pick PostgreSQL or MySQL on the first-start page or in **Settings > Advanced**. MongoDB is not supported by the task runner.

For a single image without compose: `docker build -t cognitrix .` then `docker run -p 8000:8000 -v cognitrix-data:/home/cognitrix/.cognitrix cognitrix`. Without Redis, it uses the built-in queue and starts its own worker.

### Fly.io

`fly.toml` deploys the single-container shape to [Fly.io](https://fly.io): one always-on machine running the web process and a Celery worker under Supervisor. Pick Redis on the first-start page (or in **Settings > Advanced**) to use an external Redis. SQLite lives on the mounted volume, so keep the web process to one machine. To run the agents' sandboxed computers as Fly Machines too, see [docs/sandbox-on-fly.md](docs/sandbox-on-fly.md).

## API access

Cognitrix exposes an HTTP API for programmatic use by external apps, scripts, and automation platforms. Create an **API key** from the **API Keys** page in the web UI (or via `POST /api/v1/api-keys` with a session token). A key is shown **once** at creation — copy the key and its webhook signing secret then.

Keys carry fine-grained permissions:

- **Scopes** — `chat` (call agents), `run` (start/cancel tasks and teams), `read` (GET resources), `write` (create/edit/delete).
- **Allowlists** — optional agent/team restrictions (empty = all). Allowlists constrain invoke paths; `write` is full CRUD.

Send the key as `Authorization: Bearer ctx_…` or `X-API-Key: ctx_…`. Missing/invalid/revoked/expired credentials return `401`; a valid key lacking a scope or allowlist entry returns `403`.

### Chat with an agent

```bash
curl -X POST http://localhost:8000/api/v1/agents/<agent_id>/generate \
  -H "Authorization: Bearer ctx_…" -H "Content-Type: application/json" \
  -d '{"message": "Hello", "session_id": null}'
# -> {"reply": "...", "session_id": "..."}   (pass session_id back to continue)
```

Add `"stream": true` for a Server-Sent-Events token stream (`event: chunk` … `event: done`).

### Run a team or task

```bash
curl -X POST http://localhost:8000/api/v1/teams/<team_id>/run \
  -H "Authorization: Bearer ctx_…" -H "Content-Type: application/json" \
  -d '{"description": "Summarize Q3 metrics", "callback_url": "https://my.app/hook"}'
# -> 202 {"task_id": "..."}
```

Poll `GET /api/v1/tasks/<task_id>` and `GET /api/v1/tasks/<task_id>/runs`, or supply a `callback_url` to receive a webhook when the run finishes. `POST /api/v1/tasks/<task_id>/run` starts a pre-created task the same way.

### Schedule a task

Tasks can run themselves: one-shot ("at a time") or recurring (fixed interval or cron). Schedule fields ride the normal task payload:

```bash
# every 6 hours
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Authorization: Bearer ctx_…" -H "Content-Type: application/json" \
  -d '{"title": "Nightly digest", "description": "Summarize new issues",
       "assigned_agents": ["<agent_id>"], "schedule_interval": 21600}'

# weekdays at 9am (server-local time)
#   "schedule_cron": "0 9 * * 1-5"
# once, at a specific time (any ISO datetime; offsets are normalized to UTC)
#   "schedule_at": "2026-08-01T09:00:00+02:00"
```

At most one of `schedule_at` / `schedule_interval` (seconds, min 60) / `schedule_cron` may be set. Setting one arms the schedule (`schedule_enabled` defaults true); the response carries `next_run_at` (UTC). Pause/resume with `POST /api/v1/tasks/<task_id>/schedule` `{"enabled": false}` — resuming recomputes `next_run_at`.

Semantics: cron is evaluated in the server's local timezone; one-shot times are stored as UTC instants. If the server was down when a run was due, it fires **once** on startup (no backfill). If a run is still active when the next occurrence comes due, recurring schedules skip that occurrence; one-shots wait and fire when the run ends. Each fire creates a normal TaskRun, and the task's `callback_url` webhook applies. Scheduling via API key requires the `run` scope plus agent/team allowlists, same as starting a task directly.

### Webhook verification

Deliveries carry `X-Cognitrix-Timestamp` and `X-Cognitrix-Signature: sha256=<hmac>`. The HMAC-SHA256 is computed over `"{timestamp}.{raw_body}"` with the key's webhook secret — recompute and compare in constant time, and reject stale timestamps:

```python
import hmac, hashlib

def verify(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

By default webhook targets on loopback/private/link-local addresses are rejected (SSRF guard); turn on **Let webhooks call private network addresses** in Settings > Advanced to allow them in local/dev setups.

### OpenAI-compatible endpoint

Point any OpenAI SDK at `<host>/v1` with your key as the API key:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="ctx_…")
client.chat.completions.create(
    model="Assistant",  # an agent name (see GET /v1/models)
    messages=[{"role": "user", "content": "Hello"}],
)
```

Both blocking and `stream=True` are supported. Requires the `chat` scope.

## Development

Run the engine and the web UI's dev server side by side:

```bash
pip install -e .
cognitrix --ui web                 # API on http://localhost:8000

cd frontend
pnpm install
pnpm run dev                       # UI on http://localhost:5173, proxies /api to :8000
```

Tests: `pytest` for the engine and `pnpm test` in `frontend/` for the UI. Lint with `ruff check .`.

The desktop app is a Tauri shell around the same UI with the engine bundled by PyInstaller; build steps are in [desktop/README.md](desktop/README.md). The sandbox image is built from [sandbox/desktop/Dockerfile](sandbox/desktop/Dockerfile).

More docs:

- [Pair your PC with a web server](docs/pair-your-pc.md)
- [Sign in with a code](docs/sign-in-with-a-code.md)
- [Run the sandboxed computers on Fly.io](docs/sandbox-on-fly.md)
- [MCP servers](MCP_DYNAMIC_SERVERS.md)
- Release notes for each version are in [cognitrix/release_notes](cognitrix/release_notes)

## Contributing

Cognitrix is open source and contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Acknowledgments

Cognitrix was created by [Amos Amissah](https://github.com/theonlyamos), inspired by projects like AutoGPT and GPTEngineer. Thanks to the open-source community and the AI companies whose APIs it builds on.
