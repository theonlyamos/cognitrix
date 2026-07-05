# Schedulable Tasks — Design

Date: 2026-07-05. Status: approved.

## Problem

Tasks run only on demand (UI Start, `POST /tasks/{id}/run`, autostart-on-save). No way to run a task at a future time or on a recurring schedule — nightly reports, periodic maintenance, "run this Friday 9am".

## Decisions

- **Schedule shapes**: one-shot ("run at T") + recurring (interval "every N seconds" AND 5-field cron expressions).
- **Engine**: in-app asyncio loop started by a FastAPI lifespan hook (Approach A). No Celery beat, no APScheduler — schedule state lives as columns on the Task row, firing reuses the existing Celery enqueue path (`_enqueue_task_start`), run history (TaskRun) and completion webhooks unchanged.
- **Missed while down**: catch-up once. An overdue `next_run_at` fires exactly once on the next tick; recurring schedules then advance from *now* — no backfill storms.
- **New dependency**: `croniter` only.

## Data model

Five nullable columns on Task (no new table; one schedule per task):

| field | meaning |
|---|---|
| `schedule_at` | one-shot: naive-UTC `'YYYY-MM-DD HH:MM:SS'` |
| `schedule_interval` | recurring: every N seconds (floor 60) |
| `schedule_cron` | recurring: 5-field cron expression |
| `next_run_at` | engine's single dispatch column, all types |
| `schedule_enabled` | pause/resume toggle |

At most one of `at`/`interval`/`cron` set (422 otherwise). `schedule_enabled` null-coerces (None→False) so pre-migration rows load. Columns added via the existing `_ensure_schema` ALTER loop.

## Semantics

- **Timezones**: clients send local wall-clock converted to UTC; server normalizes any `schedule_at` input (offset-aware ISO → naive UTC `'%Y-%m-%d %H:%M:%S'`, the `normalize_expiry` pattern). Cron is evaluated in **server-local time** ("0 9 * * *" = 9am on the server clock), result stored as naive UTC.
- **Firing**: scheduler tick (~20s) claims a due task by compare-and-set on `(id, next_run_at)` — rowcount 0 = lost race, skip. The claim advances the schedule (recurring: next occurrence from now; one-shot: `schedule_enabled=False, next_run_at=None`) *before* enqueueing, then enqueues through `_enqueue_task_start`.
- **Overlap** (task already running, 409): recurring → skip the occurrence; one-shot → revert the claim and retry each tick, so "run at 9am" fires when the active run ends instead of never.
- **Broker down (503)**: revert the claim for all types; retries next tick, fires once when the broker returns.
- **Failure isolation**: per-task try/except inside the tick (bad data skips one task) and a try/except around the tick in the loop (a DB error can't kill the scheduler).
- **Webhooks**: scheduled runs fire the existing completion webhook when `callback_url` is set — no new webhook code.
- Ceiling (documented): per-process scheduler — single uvicorn worker assumed; multi-instance needs a distributed lock.

## API

- **Lifespan** (`api/main.py`): `initialize_database()` (idempotent) + start/cancel the scheduler loop. The Celery worker never imports `api.main`, so the loop runs only in the web app.
- **`_enqueue_task_start`** switches its final full-row `task.save()` to a partial `update_one({'id'}, {status, pid})` — a full-row write would revert scheduler claims and already clobbers concurrent edits on manual starts today.
- **`POST /tasks` (save_task)**, keyed on `model_fields_set`:
  - No schedule field in payload → carry over all 5 schedule fields + `callback_url`/`callback_key_id` from the stored row; no recompute (title-only edits don't push schedules back).
  - Any schedule field in payload → full respecification: type switching works, `schedule_enabled` defaults True when a type is set but omitted, `schedule_at` normalized, validated (single type, interval ≥60s, cron syntax, one-shot not in the past — past check only on respecification), `next_run_at` recomputed.
  - API-key guard: schedule respecified OR stored schedule enabled → requires `run` scope + agent/team allowlists (mirror of the autostart guard; a `write`-only key must not program scheduled agent execution).
- **`POST /tasks/{id}/schedule`** `{enabled: bool}`: pause/resume via partial update. Resume recomputes `next_run_at`; resuming a past one-shot → 422 (no stale instant fire). Same API-key guard when enabling.

## Frontend

- **TaskPage**: Schedule section — mode select (none / at time / every / cron), conditional inputs (`datetime-local` with `min`, N+unit, cron text), enabled checkbox. Payload always includes all 5 fields (form is authoritative for schedule state).
- **Tasks list**: next-run chip beside the status badge (`schedule_enabled && next_run_at`, parsed with explicit `+'Z'` — naive-UTC strings must not be parsed as local).
- **TaskDetail**: next-run chip + Pause/Resume button, refetching the task after toggle.

## Testing

Unit: validation matrix, next-run math (incl. cron local→UTC), `schedule_at` normalization, tick CAS win/lose, 409 one-shot-revert vs recurring-skip, 503 revert, one-shot disables after fire, bad `next_run_at` string skips without killing the tick, carry-over vs respecification in save_task, API-key schedule guard, past-one-shot resume 422. E2e: interval task fires and advances; one-shot fires once and disables; pause/resume; scheduled run delivers webhook; unscheduled start/cancel unchanged.
