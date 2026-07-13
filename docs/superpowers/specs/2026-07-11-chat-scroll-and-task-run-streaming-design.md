# Chat Scroll Containment and Task-Run Streaming Design

Date: 2026-07-11
Status: approved

## Goal

Keep long chat conversations inside the chat transcript instead of allowing the
application shell or document to scroll. Stream task-run agent text, tool
activity, and step state to the task detail page while a Celery worker is still
executing, preserve that progress across reconnects, and render completed agent
output as Markdown.

## Confirmed Root Causes

### Chat page

`AppLayout` owns a fixed `100dvh` shell, but `Home` creates a nested `100vh`
page. On viewports where those units differ, `Home` has an outer scroll range.
The message effect calls `scrollIntoView()` on a bottom sentinel; that API can
scroll every eligible ancestor, so a long transcript can move the shell or
document and expose the body below the application.

The chat column and transcript also lack an explicit `min-h-0` containment
chain. The intended transcript has `overflow-y-auto`, but its ancestors do not
fully state that it is the only vertical scroll owner.

### Task-run page

Providers already yield text deltas, and `Session.__call__` already emits text,
tool-start, and tool-completion callbacks. Task execution replaces the browser
transport with `_run_agent_turn.capture`, which concatenates only `content`
into a local string. Tool events have no `content`, so they are discarded.

The step session remains in the Celery worker's memory until the entire turn is
saved. `TaskDetail` polls persisted chat every five seconds, so it sees an empty
session until the turn finishes. Parallel step results are also applied only
after the whole `asyncio.gather` batch completes.

The existing chat SSE manager cannot bridge this gap because it is an
API-process in-memory queue, while task runs execute in a separate local or
external Celery worker.

## Decisions

- Use a durable database event log plus an authenticated run-scoped SSE
  endpoint. This works with both Redis and the filesystem Celery fallback.
- Give every event a monotonically increasing sequence within its run so a
  reconnect can replay events without gaps or duplicates.
- Batch text deltas by time and size; do not create one database row per token.
- Persist tool and lifecycle events immediately.
- Keep the saved `Session.chat` transcript as the canonical completed history.
  Live events are reconciled with that transcript when a turn completes.
- Keep five-second REST polling as a fallback for task/run lifecycle state.
- Render active text as plain text and switch to the existing Markdown renderer
  only after the turn is complete.
- Keep synthesis non-streaming. Its completed output is rendered as Markdown.
- Event-transport failures must never fail the underlying task execution.

## Chat Scroll Containment

`AppLayout` remains the sole viewport owner. `Home` changes from a nested
`h-screen` page to a contained child using `h-full min-h-0 overflow-hidden`.
The conversation sidebar, chat column, and transcript receive `min-h-0` where
needed. Only the transcript keeps `overflow-y-auto`; it also receives
`overscroll-contain` so wheel and touch momentum do not chain into an ancestor.

The transcript element receives a ref. The message effect sets that element's
`scrollTop` to its `scrollHeight` instead of calling `scrollIntoView()` on a
descendant. This preserves the current auto-follow behavior while guaranteeing
that neither the shell nor `document.scrollingElement` is moved. The bottom
sentinel is removed.

## Durable Event Model

Add `TaskRunEvent`, an odbms model stored in its own table:

```text
id
run_id: str
session_id: str | None
step_index: int | None
sequence: int
kind: str
agent_name: str | None
data: dict
created_at
updated_at
```

`sequence` starts at one for each new run. A single `TaskRunEventEmitter`
instance is shared by the run's parallel step coroutines. An `asyncio.Lock`
serializes sequence allocation and inserts, providing one total event order
without serializing the actual agent work.

Supported event kinds and data are:

| Kind | Required data |
| --- | --- |
| `step_status` | `status`, `title`, `attempts` |
| `text_delta` | `turn_id`, `attempt`, `content` |
| `tool_started` | `turn_id`, `tool_call_id`, `tool_name`, `params` |
| `tool_completed` | `turn_id`, `tool_call_id`, `tool_name`, `result`, `status` |
| `turn_completed` | `turn_id`, `attempt` |
| `run_status` | `status`, optional `error` |

Tool parameters and results retain the existing 4,000-character preview cap.
Text is accumulated per session and turn. The first non-empty chunk is emitted
immediately; later chunks flush when either 150 milliseconds have elapsed or
256 characters are buffered. The remainder is flushed before a tool event or
turn completion. Event rows remain associated with run history; batching keeps
their count proportional to meaningful output rather than provider tokens.

Only executor and retry turns publish user-visible progress. Evaluator turns
remain in the final session transcript but do not stream their internal score
JSON to the live task interface.

## Orchestrator Integration

The emitter is created after the `TaskRun` row exists and is passed through
step execution. `_run_agent_turn` continues capturing the final answer for
dependency prompts and gating, while also forwarding text and tool payloads to
the emitter.

Step lifecycle events are written at these boundaries:

1. `running` immediately before the step coroutine starts.
2. Text and tool events during each executor turn.
3. `turn_completed` after the session has saved its canonical turn.
4. `done`, `failed`, or `cancelled` as soon as that coroutine finishes.

Parallel coroutines are consumed with completion-order processing rather than
waiting to update every step after the whole batch. Each completed result is
applied to the plan and persisted immediately. Remaining steps continue with
the same concurrency and dependency semantics as today.

Before a run becomes terminal, the emitter flushes pending text and writes the
terminal `run_status` event. Event insertion catches and logs database errors;
the original task result, status, and canonical session save still proceed.

## SSE API

Add:

```text
GET /api/v1/tasks/{task_id}/runs/{run_id}/events
```

The endpoint:

- Requires read authorization, verifies the run belongs to the task, and
  applies the task's team/agent allowlists.
- Accepts either `Last-Event-ID` or `?after=<sequence>`, using the greatest
  valid value when both are supplied.
- Replays all events with a greater sequence in ascending order, then polls the
  event table for new rows every 500 milliseconds.
- Emits each row as an SSE `task_run` event whose `id` is the decimal sequence
  and whose JSON data contains the complete event projection.
- Emits heartbeat pings while idle.
- Ends only after a terminal run status has been observed and all persisted
  events through that status have been sent.
- Stops promptly when the client disconnects.

An unknown task or run returns 404. A run/task mismatch also returns 404 rather
than disclosing that the run exists. Invalid replay cursors are treated as zero.

## Frontend Event Transport

Extract the authenticated fetch/reader/reconnect logic from `useSSE` into a
configurable `useEventStream` hook. The existing chat hook retains its current
public API and delegates to the shared reader, preventing chat behavior from
changing as a side effect.

The shared reader supports:

- A caller-provided API path.
- SSE `id`, `event`, and multi-line `data` fields.
- `Last-Event-ID` on reconnect.
- Exponential retry, cancellation cleanup, manual reconnect, and heartbeat
  comments.
- Delivery of every valid event to the caller; domain-specific filtering stays
  in `useSSE` and `useTaskRunEvents`.

`useTaskRunEvents(runId)` keeps the latest sequence in a ref and rejects a
duplicate or older sequence. It exposes connection/error state plus typed
`TaskRunEvent` objects to `TaskDetail`.

## Task Detail State and Reconciliation

`TaskDetail` subscribes only to the active selected run. A pure reducer groups
events by session, step, and turn:

- `text_delta` appends to one live assistant entry for its `turn_id`.
- `tool_started` creates a visible running tool entry.
- `tool_completed` updates the matching tool with its result and terminal
  status.
- `step_status` updates the immediate visual state and triggers a silent run
  summary refresh.
- `turn_completed` reloads `/sessions/{session_id}/chat`.

After the canonical chat reload succeeds, live entries for that session and
turn are removed. If the reload fails, the live entries remain visible and the
existing polling path retries later. A terminal `run_status` triggers final
task, run, session-map, and selected-transcript reconciliation.

The existing automatic selection of a running step remains. A manual selection
still wins, and events for other parallel steps are retained until the user
selects them.

## Transcript Rendering

Extend the transcript entry shape so assistant entries can be marked `live`
and tool entries can carry running/completed/failed state. `TranscriptView`
uses the existing `MarkdownMessage` component for completed assistant content.
Live assistant content remains `whitespace-pre-wrap` plain text to avoid
re-running Markdown parsing and syntax highlighting for every delta.

Tool names, parameters, and results remain React text inside expandable rows;
they are never interpreted as HTML or Markdown. The current ReactMarkdown
configuration remains unchanged and does not enable raw HTML.

The transcript container becomes `role="log"` with a polite live region so new
agent/tool activity is announced without interrupting other controls.

## Error Handling and Security

- Event persistence is best effort and isolated from task success/failure.
- SSE failures show a reconnect action while REST polling continues.
- Replay is idempotent by sequence; duplicate deliveries do not duplicate UI
  output.
- Event payloads never include provider credentials or uncapped tool output.
- The stream uses the same task authorization and allowlist rules as other task
  reads.
- Markdown raw HTML stays disabled, external links retain safe target/rel
  attributes, and tool payloads stay escaped.

## Testing

Backend tests must verify:

- Sequence allocation remains ordered across concurrent step emitters.
- The first text delta appears before the agent turn returns, later deltas are
  batched, and the remainder flushes at completion.
- Tool start and completion events retain IDs, caps, and ordering.
- Evaluator turns do not publish live text.
- One parallel step can persist `done` while another remains `running`.
- Event-write failure does not change the task outcome.
- SSE authorization, task/run mismatch, replay cursor, ordered tailing,
  heartbeat, terminal completion, and disconnect behavior.
- Database initialization creates the event table in both API and worker
  startup paths.

Frontend tests must verify:

- `Home` contains the viewport, only the transcript owns vertical overflow,
  and message updates change transcript `scrollTop` without calling
  `scrollIntoView()` or moving the document.
- The generic event reader parses IDs, multi-line data, split chunks, reconnect
  cursors, malformed events, and cleanup without changing chat SSE behavior.
- Task-run event reduction appends text, pairs tool start/result events, rejects
  duplicate sequences, and preserves parallel-step output.
- `TaskDetail` reconciles a completed live turn with canonical session chat and
  keeps live output when that refresh fails.
- Live output is plain text; completed output renders headings, lists, links,
  and fenced code through `MarkdownMessage`.
- Existing responsive, accessibility, chat, task-run, lint, type-check, build,
  and backend suites remain green.

Browser verification must use the running application at
`http://localhost:5173`: confirm a long conversation scrolls only inside the
transcript, then run a tool-using task and observe text/tool progress before its
step completes. Reload during the run and confirm the event replay reconstructs
the visible progress without duplication.

## Non-goals

- No Redis-only transport or requirement.
- No change to task planning, assignment, gate thresholds, or dependency
  semantics beyond persisting parallel completions as they arrive.
- No streaming of evaluator internals.
- No streaming rewrite of synthesis.
- No raw-HTML Markdown support.
- No redesign of historical task-run navigation.
