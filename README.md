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
