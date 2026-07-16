# Cognitrix

Cognitrix is an open-source autonomous AI agents orchestrator built in Python. It allows you to create and manage AI agents with ease and integrates seamlessly with large language models (LLMs) from various providers.

## Features

- **Agent Creation and Management:** Create, list, and load AI agents with customizable names, tasks, and configurations.
- **LLM Integration:** Integrates with multiple LLM providers via OpenRouter (200+ models including OpenAI, Anthropic, Google, Meta, Mistral) plus local models via Ollama.
- **Modular Architecture:** Easily extensible, allowing the addition of new tools, agents, and LLM integrations.
- **Conversational Interface:** Interact with AI agents through a command-line interface, providing queries and receiving responses.
- **Tool Integration:** Agents can utilize a variety of tools, including calculators, web searches, file system browsers, and more.
- **Autonomous Agent Mode:** Agents can operate autonomously, visually perceiving the screen, interacting with UI elements, and performing tasks.
- **Multimodal Support:** Handles both text and image inputs/outputs, enabling multimodal interactions.

## Architecture

Cognitrix's architecture is designed to be highly modular and extensible:

- **Agents:** The base `Agent` class is for creating and managing AI agents.
- **LLMs:** Provider classes for OpenRouter (primary - 200+ models), OpenAI (direct), and Ollama (local).
- **Tools:** A set of tools that agents can utilize. Each tool has a `category` attribute for grouping and management.
- **Templates:** Customizable prompt templates guide the behavior and output formats of LLMs.

More tools can be added by creating new classes that inherit from the `Tool` base class and specifying a unique `category`.

## Installation

**Prerequisites**

- **Python 3.11–3.13** (the package will not build on 3.10 or lower).
- **Node.js 18+** and a package manager (this repo uses **pnpm**) — only needed to build the web UI from source.
- **Poetry** — only needed for building from source.
- **Redis** — only needed if you use the background task API (Celery worker).

**Install with pip**:

```bash
pip install cognitrix
```

**Build from source**:

```bash
git clone https://github.com/theonlyamos/cognitrix.git
cd cognitrix/frontend
pnpm install
pnpm run build
cd ..
pip install .
```

**Install directly from github**:

```bash
pip install https://github.com/theonlyamos/cognitrix/archive/main.zip
```

**Build the Docker Image**:

```bash
git clone https://github.com/theonlyamos/cognitrix.git
cd cognitrix
docker build -t cognitrix .
```

## Usage

**Fill these environment variables as needed**

```bash
OPENAI_API_KEY=
CO_API_KEY=
TAVILY_API_KEY=
CLARIFAI_ACCESS_TOKEN=
GROQ_API_KEY=
GOOGLE_API_KEY=
NEWSAPI_API_KEY=
ANTHROPIC_API_KEY=
DEEPGRAM_API_KEY=
MINDSDB_API_KEY=
BRAVE_SEARCH_API_KEY=
AIMLAPI_API_KEY=
```

To run Cognitrix with default settings:

```bash
cognitrix
```

**Access the Web UI**

To run with web interface

```bash
cognitrix --ui web
```

Open your web browser and go to `http://localhost:8000` to access the web UI.

**Run the Docker Container**

After building the image, you can run the container with:

```bash
docker run -p 8000:8000 cognitrix
```

This command maps port 8000 of the container to port 8000 on your host machine.

To choose an LLM provider, pass `--provider` (config is read from the environment —
`AI_PROVIDER`, `<PROVIDER>_BASE_URL`, `<PROVIDER>_API_KEY`, `<PROVIDER>_MODEL`):

```bash
cognitrix --provider openrouter
```

To list created agents:

```bash
cognitrix agents -l
```

To list available tools:

```bash
cognitrix tools -l
```

To run Cognitrix with a specific provider:

```bash
cognitrix --provider <provider_name>
```

To run Cognitrix with a specific agent:

```bash
cognitrix --agent <agent_name>
```

To run Cognitrix with a category of tools:

```bash
cognitrix --load-tools "web"
```

To run Cognitrix with categories of tools:

```bash
cognitrix --load-tools "web,general"
```

To create a new agent:

```bash
cognitrix agents --new
```

For more options and usage details, use the help command:

```bash
cognitrix --help
```

## Web UI

Cognitrix includes a web UI built with React, TypeScript, and Vite. This UI allows users to interact with the AI agents and manage their configurations easily.

### Development

For development purposes, you can also run the web UI locally without Docker. Ensure you have Node.js 18+ and pnpm installed, then follow these steps:

1. **Install Dependencies**:
   Navigate to the `frontend` directory and run:

   ```bash
   pnpm install
   ```

2. **Start the Development Server**:
   Run the following command to start the development server:

   ```bash
   pnpm run dev
   ```

3. **Access the Development Server**:
   Open your web browser and go to `http://localhost:5173` to view the web UI in development mode.

## Docker deployment

`docker-compose.yml` runs three services: the **web** app (FastAPI + the in-process schedule loop), a dedicated **worker** (Celery, executes task/team runs), and **Redis** as the broker. Web and worker share the SQLite app database over a named volume.

```bash
cp .env.example .env          # fill in provider keys + a JWT_SECRET_KEY
docker compose up --build
```

The UI is then at `http://localhost:8000`.

- **Provider keys / config** come from `.env` (injected into both web and worker). `COGNITRIX_ENV=production` is set in compose, so `JWT_SECRET_KEY` is required — generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **Scale execution** with `docker compose up --scale worker=3`. Keep **web at a single replica**: the schedule tick loop runs per-process, so multiple web containers would fire the same due schedules more than once.
- **State** lives in the `cognitrix-data` volume (SQLite DB, JWT secret, MCP config) and `redis-data` (broker). For heavier task concurrency, point `DB_*` at PostgreSQL or MySQL instead of the shared SQLite file. MongoDB is not supported by the durable task runner because its cross-table lease fencing requires relational atomic predicates.
- Without `CELERY_BROKER_URL`, a single container falls back to an in-process filesystem broker + auto-spawned worker — handy for a one-container deploy, but the dedicated-worker compose stack is the recommended setup.

### Fly.io

`fly.toml` deploys the single-container shape to [Fly.io](https://fly.io): one always-warm machine (web + scheduler + auto-spawned worker) with SQLite on a volume, built by Fly's own remote builder. The setup steps (`fly volumes create`, `fly secrets set`, `fly deploy`) are in the file's header comment. Keep it to one machine — the schedule loop is per-process.

## API Access

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

By default webhook targets on loopback/private/link-local addresses are rejected (SSRF guard); set `COGNITRIX_WEBHOOK_ALLOW_PRIVATE=1` to allow them in local/dev setups.

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

## Contributing

Cognitrix is open source and contributions are welcome! Please refer to [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for more information.

## Acknowledgments

Cognitrix was created by [Amos Amissah](https://github.com/theonlyamos) and is inspired by projects like AutoGPT and GPTEngineer. Special thanks to the open-source community and AI companies providing LLM APIs.
