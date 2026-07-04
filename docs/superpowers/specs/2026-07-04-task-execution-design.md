# Task Execution Redesign

Date: 2026-07-04
Status: approved

## Context

Task execution has three parallel implementations, and the live one is the weakest:

| System | Capabilities | Status |
|---|---|---|
| `Task.start()` (tasks/base.py) | Sequential fixed steps, one primary agent (`team[0]`) + Evaluator whose score is ignored, no routing/verification/results | Live — used by ▶ Run and autostart |
| `TeamManager.work_on_task` (teams/base.py) | AgentRouter, StructuredPlanner, WorkflowExecutor (parallel, dependency-aware), writes `task.results` | Dead — reachable only via an unused websocket path; finalize stubbed |
| `handle_multi_step_task` (tasks/handler.py) | Plan generation, topological ordering, `verify_step`, `synthesize_results`, progress tracker | Live, but chat-only |

A live team test (4-step multi-domain run) exposed the consequences: the team's
`leader_id` is ignored, delegation happens only if the primary agent decides to
call a teammate, the Evaluator doubles LLM cost per step for an unused score,
`Task.results` is never populated, and there is no cancel, resume, or per-step
timeout.

## Decisions (user-approved)

- **All four priorities**: smarter orchestration, robustness/control, real
  outcomes, cost/speed.
- **One engine for everything**: ▶ Run, autostart, AND chat multi-step use the
  new orchestrator. The naive loop, the dead team path, and the handler
  internals are deleted.
- **Session per step**: a run is a group of per-step sessions, not one linear
  transcript.
- **Evaluator as gate**: keep the Evaluator turn per step, but its `finalscore`
  gates completion (threshold, retry with feedback, fail). Empty answers
  short-circuit before wasting an evaluator call.

## Data model

**New model `TaskRun`** (odbms `Model`):

```
id, task_id, status: running|completed|failed|cancelled|cancelling,
plan: list[dict], result: str|None, error: str|None,
started_at, completed_at
```

- `plan` is the executed snapshot: `[{index, title, description, agent_name,
  dependencies: [int], status: pending|running|done|failed|skipped|cancelled,
  attempts}]`. `Task.step_instructions` remains the authoring *template*;
  per-run progress lives in `TaskRun.plan`. This removes the done-flag reset
  hack on `/tasks/start` and makes run history unambiguous (today
  "interrupted" is inferred from missing `completed_at`).
- **`Session` gains** `run_id`, `step_index`, `step_title` (all nullable).
  One session per step; `agent_id` = the step's assigned agent.
- `Task.results` finally populated: each completed run appends its synthesis.

## Orchestrator pipeline — `cognitrix/tasks/orchestrator.py`

1. **Plan** — use template steps when present; otherwise `StructuredPlanner`
   generates steps + dependencies from the task description. Validate the
   generated plan (≤10 steps, non-empty titles); on invalid output fall back
   to a single step built from the description. Snapshot into `TaskRun.plan`.
2. **Assign** — the leader (team `leader_id` if the task has a team, else the
   first assigned agent) makes ONE LLM call: agent roster (names, system
   prompts, tools) + plan → agent per step. Unassigned/unmatched steps fall
   back to the leader. (No per-step router calls.)
3. **Execute** — dependency-ready batches, `asyncio.Semaphore(3)`, fresh
   `Session(task_id, run_id, step_index, step_title, agent_id)` per step.
   Step prompt includes the results of its dependency steps. Per-step timeout
   (default 10 min) via `asyncio.wait_for`; timeout = step failure. Cancel is
   checked from the DB between steps and between attempts, so it works across
   processes (celery worker vs API).
4. **Gate** — empty answer → retry once with backoff (no evaluator call).
   Non-empty → Evaluator turn in the same step session; parse `finalscore`;
   ≥7 → step done; <7 → retry the step once with the evaluator's suggestions
   injected into the prompt; still <7 → step failed → cancel pending steps →
   run `failed` (+`TaskRun.error`) → task `FAILED`.
5. **Synthesize** — leader turn over all step results → `TaskRun.result`,
   append to `task.results`, run `completed`, task `COMPLETED`.

## API

- `GET /tasks/start/{id}` unchanged externally (celery/autostart dispatch the
  same; the worker now calls the orchestrator). `?resume=true` → new run that
  copies the last failed run's plan and skips its `done` steps.
- New `POST /tasks/{id}/cancel` — flips the active run to `cancelling`.
- New `GET /tasks/{id}/runs` — `TaskRun` list (real statuses, no inference).
- New `GET /sessions/runs/{run_id}` — the step sessions of one run
  (summary shape, consistent with the other session list endpoints).

## Chat multi-step integration

`is_multi_step_task` detection stays. `handle_multi_step_task` becomes a thin
wrapper: create a real `Task` (steps from the planner), run the orchestrator
in-process, post the synthesis back into the conversation, and include a link
to the task's run page. Chat multi-step gains history, monitoring, and named
steps for free. The handler's own plan/execute/verify/tracker internals are
deleted.

## UI (TaskDetail evolves)

- Runs sidebar reads `TaskRun` records — badges for
  completed/failed/cancelled/running with no client-side inference.
- Selected run shows a **steps panel**: each plan step with a status icon
  (⋯ running / ✓ done / ✕ failed / ↷ skipped) and its assigned agent's name;
  parallel steps visibly run together. Clicking a step opens that step's
  transcript in the pane (existing `TranscriptView`, already name-aware).
- Header actions: ▶ Run · Resume (shown when the last run failed) · Cancel
  (shown while running). `cancelled` badge added wherever status renders.
- Polling keeps today's shape: task + active run's plan statuses + the
  selected step's chat only, 5s cadence with the existing back-off.
- Legacy runs (pre-redesign sessions with `task_id` but no `run_id`) remain
  listed via a small fallback section so old history isn't orphaned.

## Deletions

- `Task.start()` body → `await orchestrator.run(self)` (call sites unchanged).
- `TeamManager.work_on_task`, `leader_create_workflow`,
  `leader_coordinate_workflow`, `leader_evaluate_and_finalize`,
  `WorkflowExecutor`, celery `run_team_task` — deleted.
- `tasks/handler.py` internals (`execute_step`, `verify_step`,
  `synthesize_results`, tracker usage) — deleted; wrapper remains.
- `AgentRouter` is untouched unless it ends up import-dead, decided at
  implementation time.

## Failure semantics

- Step exception or timeout = step failure → same path as a gate failure.
- Worker dies mid-run: run stays `running`; Cancel is always available and the
  UI already backs off polling. No automatic reaper (out of scope).
- Rate-limit empty turns: the existing retry-with-backoff behavior is folded
  into the gate (step 4 above).

## Migration risks

- New `Session` columns + new `TaskRun` table on sqlite: **verify odbms adds
  columns to existing tables at init**; if not, add an init-time
  `ALTER TABLE` shim next to the existing odbms compat patches in
  `config.py`.
- sqlite concurrent writes from parallel steps serialize through the single
  shared aiosqlite connection — acceptable.
- Chat multi-step behavior change is deliberate and user-approved.

## Testing

E2E via API + browser preview (patterns established in this repo):

1. Task with 3 independent steps → executions overlap (timestamps).
2. Step-less task → planner generates a valid plan.
3. Gate failure (agent with bogus model) → run `failed`, task `FAILED`,
   pending steps `cancelled`.
4. Cancel mid-run → run `cancelled`, no further step sessions.
5. Resume after failure → done steps skipped, run completes.
6. Chat multi-step message → task created, link posted, synthesis in chat.
7. Team task → per-step assigned agents' names in transcripts.
8. UI: steps panel updates live, parallel indicators, step-click transcripts.

Regressions: single-step happy path, run history rendering, plain chat.

## Out of scope

- Automatic stale-run reaper.
- User-scoped sessions/tasks (pre-existing gap).
- Model/provider changes for the team agents.
