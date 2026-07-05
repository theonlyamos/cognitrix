# API Authentication + Programmatic Agent/Team Access — Design

**Date:** 2026-07-05
**Status:** Approved

## Problem

Cognitrix's API serves only the web UI: every route requires a short-lived login JWT, and chat runs over an SSE action-queue tied to the browser client. A hosted deployment offers no way for an external app, script, or automation platform to authenticate durably or invoke an agent/team programmatically.

## Goals

- Long-lived, revocable API credentials with fine-grained permissions.
- Programmatic agent chat (blocking and streaming, stateful sessions).
- Programmatic team/task execution with completion webhooks.
- OpenAI-compatible endpoint so existing SDKs work unchanged.
- API keys usable everywhere the web UI's JWT works (full CRUD), governed by scopes.
- Key management from the web UI.

## Non-goals

- Multi-tenancy or per-user resource ownership (agents/teams remain shared per deployment).
- Distributed rate limiting (in-process only; Redis-backed window is a later upgrade).
- Full OpenAI API surface (only `/v1/chat/completions` + `/v1/models`).
- Async job decoupling of chat (blocking with timeout is acceptable).

## Design

### Credential model

`APIKey` (odbms Model, table `apikeys`): `name`, `user_id`, `key_hash` (sha256 hex of the full secret), `prefix` (display), `scopes` (subset of `chat`, `run`, `read`, `write`), `allowed_agents` / `allowed_teams` (ids; empty = all), `webhook_secret` (HMAC signing key, server-side plaintext by necessity), `rate_limit` (req/min override), `expires_at` (naive-UTC string), `last_used_at`, `revoked`.

Key format `ctx_<token_urlsafe(32)>`. The secret and webhook secret are returned exactly once at creation; only the hash is stored. Verification is a `find_one` on the hash — the secret's entropy makes timing on the SQL comparison useless.

Scope semantics:
- `read` — GET on CRUD resources.
- `write` — mutations on CRUD resources. Full CRUD is deliberate: allowlists do NOT constrain `write` (a write key can edit any agent). Allowlists constrain invoke paths only.
- `chat` — agent generate endpoints (incl. the OpenAI shim).
- `run` — task/team execution (start, cancel, run).

### Unified auth

One dependency, `get_auth_context`, parses `Authorization: Bearer …` or `X-API-Key` manually (no `OAuth2PasswordBearer` — its auto-error would pre-reject key requests). `ctx_`-prefixed tokens take the key path (hash lookup → revoked/expired → rate limit → throttled last-used stamp); anything else takes the existing JWT path. Both paths strip the user's password hash. The result is an `AuthContext` (`user`, `api_key`, `has_scope`, `agent_allowed`, `team_allowed`); a JWT session has `api_key=None` and passes every check.

Enforcement is structural, not stringly:
- CRUD routers carry a `crud_scope` dependency: GET/HEAD → `read`, else → `write`.
- Execute routes live on dedicated routers with explicit scope dependencies (`require('run')` for task start/cancel/run and team run; `require('chat')` for generate). Registration order keeps `/tasks/start/{id}` ahead of `/tasks/{task_id}`.
- Legacy browser plumbing (`GET /agents/sse`, `POST /agents/chat`, `GET /sessions/{id}/events`, `POST /sessions/{id}/chat`) is JWT-only — it runs tool-enabled turns and would otherwise bypass `chat` scope and allowlists. The dead, shadowed `GET /teams/generate` is deleted.
- `POST /tasks` with `autostart` from a key requires `run` scope + team allowlist (else 403) — otherwise `write` alone would execute the orchestrator.
- Key management routes are JWT-only: a key cannot mint or manage keys.
- Errors: 401 + `WWW-Authenticate` for a missing/invalid/revoked/expired credential; 403 when a valid key lacks a scope or allowlist entry (detail names what's missing).

### Rate limiting

Per-key sliding window (deque of monotonic timestamps), default `COGNITRIX_API_RATE_LIMIT` (60/min), per-key override. Applies to API-key requests only; JWT/UI traffic is exempt. In-process — a multi-worker deployment multiplies limits (accepted; documented).

### Programmatic chat

`POST /api/v1/agents/{agent_id}/generate` — scope `chat`, agent allowlist.
Body `{message, session_id?, stream? = false}`; the response always carries `session_id` for continuation.
- Blocking: run the session turn with an async capture callback (`interface='web'`, guard None chunks), bounded by `COGNITRIX_API_CHAT_TIMEOUT` (default 300 s). Timeout → the endpoint saves the session itself (the turn unwinds without saving) and returns 504. A provider dead turn (`Streaming error:` chunk) → 502. Returns `{reply, session_id}`.
- Streaming: SSE (`event: chunk` per content piece, `event: done` with the session id). Producer task bridges the turn to the response through a bounded queue; client disconnect cancels the producer and best-effort saves the session.
- `interface='web'` means risky tools are denied by the approval gate unless `COGNITRIX_AUTO_APPROVE=1` — same semantics as task execution.

### Programmatic execution + webhooks

- `POST /api/v1/teams/{team_id}/run` (`run` scope, team allowlist): creates a Task bound to the team and enqueues it via the same broker machinery as the UI's start route (shared helper). Returns 202 `{task_id}`; callers poll `GET /tasks/{id}` and `GET /tasks/{id}/runs`.
- `POST /api/v1/tasks/{task_id}/run` (`run` scope): API-first start/resume for pre-created tasks, body `{resume?, callback_url?}`. The UI's `GET /tasks/start/{id}` remains but never accepts `callback_url` — capability-bearing URLs stay out of query strings and access logs.
- Task gains nullable `callback_url` + `callback_key_id`, set only from key-authed run requests, and stripped from all task API projections (callback URLs routinely embed capability tokens).
- On any terminal run outcome (success, failure, cancel — via a try/finally at the orchestrator's exit), the worker POSTs `{task_id, run_id, status, result, error, completed_at}` to `callback_url` with `X-Cognitrix-Timestamp` and `X-Cognitrix-Signature: sha256=HMAC-SHA256("{ts}.{body}", webhook_secret)` (timestamp signed = replay protection). 3 attempts (1 s/2 s/4 s backoff, 10 s each, ~15 s total budget), never raises into the orchestrator. Revoked or expired keys are skipped.
- SSRF guard: http/https only, no redirects; loopback/private/link-local targets rejected unless `COGNITRIX_WEBHOOK_ALLOW_PRIVATE=1`. Residual DNS-rebinding gap accepted for v1.
- Known limitation: a run finalized by the worker-crash backstop (postrun handler) sends no webhook.

### OpenAI-compatible shim

`GET /v1/models` (agents as models, allowlist-filtered) and `POST /v1/chat/completions` (`model` = agent name or id), mounted at the app root before the SPA catch-all so `/v1/*` never falls through to `index.html`. Auth is the standard `Bearer ctx_…` header; scope `chat` + agent allowlist.

Incoming `messages` (including the final user message) are seeded into an ephemeral session's history with `save_history=False` — the session machinery builds prompts exclusively from stored history, and `save_history=True` would persist junk sessions. Responses use standard completion JSON; `stream: true` emits OpenAI delta chunks terminated by `data: [DONE]`. Server-side tools run as usual under the same timeout as generate.

### Key management UI

`/api/v1/api-keys` (JWT-only): list (projected fields only — never hash or secrets), create (validates scopes, normalizes expiry), revoke (soft; row kept for audit). Frontend page `ApiKeys.tsx`: key list with scope badges/expiry/last-used/revoke, create form (scope checkboxes, agent/team allowlist multi-selects, optional expiry and rate limit), and a one-time success card showing the key + webhook secret with copy buttons.

## Error handling

- Auth failures never reveal whether a key exists (uniform 401).
- Rate limit → 429 + `Retry-After`.
- Generate: 404 unknown agent/session, 403 allowlist, 502 provider dead turn, 504 timeout (session persisted).
- Run: 404 unknown team/task, 409 active run exists, 503 broker unavailable (existing semantics).
- Webhook delivery failures are logged, never fatal to the run.

## Testing

Unit: key roundtrip + expiry normalization through real sqlite saves, scope/allowlist helpers, revoked/expired rejection, ownership checks, rate-limit window, jwt_only rejections, autostart guard, webhook HMAC/retry/skip/SSRF (mock httpx), capture guards, OpenAI seeding (mock LLM must see the user text), stream shapes.
E2E: curl scope matrix per key type; blocking + streaming generate with session continuation; team run polled to completion with HMAC-verified webhook on a local listener; failure webhook; OpenAI SDK against `base_url=/v1`; full UI regression (JWT path).
