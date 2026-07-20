# Interactive Chat Questions

## Goal

Let the Assistant pause a live web-chat turn to ask one structured or free-text question, then resume the same tool loop with the user's answer. The same mechanism may ask whether long-running work should continue in chat or become a persisted background task.

This is chat-only in the first release. Task workers, CLI, TUI, and the blocking generate endpoint do not gain interactive questions.

## Assistant contract

Add a dedicated `Ask User` system tool. It accepts:

- `prompt`: required, non-empty, at most 2,000 characters;
- `details`: optional supporting text, at most 4,000 characters;
- `options`: zero to five `{id, label, description?}` items with unique stable ids;
- `allow_free_text`: whether a custom response is accepted;
- `recommended_option_id`: optional id that must refer to an option;
- `auto_submit_recommended`: optional boolean, default `false`.

At least one option or free-text input is required. Automatic submission is fixed at 60 seconds, is legal only when a recommended option exists, and is reserved by Assistant policy for low-impact, reversible choices. A submitted answer is context, never authorization: sensitive actions must still pass their normal approval gates.

The Assistant should ask only when an answer materially affects the result. For a long-running request it may ask whether to continue in chat or run in the background. Choosing background returns a normal tool result; the Assistant then explicitly calls `create_task(..., start_now=true)`.

## Runtime architecture

Create a question broker separate from the safety approval registry. It follows the proven Future-based shape without mixing question and authorization semantics:

1. SSE binds a `question_turn_ctx` containing `emit`, `session_id`, `stream_id`, and `user_key` for the active direct-chat turn.
2. `Ask User` validates its input, registers one owner-scoped pending question, emits `question_request`, and awaits its Future.
3. `POST /agents/question` resolves that Future using an action of `answer`, `cancel`, or `stop_timer`.
4. An answer becomes a structured tool result and the existing Assistant tool loop resumes.
5. Cancel makes `Ask User` raise a dedicated `asyncio.CancelledError` subtype. The existing tool-batch and Session cancellation path then persists stopped tool results and prevents another model round.

Only one question may be pending per turn. The registry is in-process, matching the existing web approval architecture. It is intentionally not durable or cross-worker in v1: process restart cancels the waiting turn cleanly. This limitation must be documented rather than hidden.

An interactive wait must not occupy the shared tool-execution limiter. Add an explicit tool capability flag whose default preserves current behavior, and mark `Ask User` as not consuming an execution slot while it awaits the browser. The LLM request has already completed before tool execution begins, so the pause also holds no provider-generation slot. The SSE turn remains active by design.

## Timer and reconnect correctness

The server owns the countdown. It emits an absolute UTC `auto_submit_at` deadline, not a relative counter. The browser derives remaining seconds from that deadline, so background-tab throttling and reconnects do not extend the timer.

The broker waits with `asyncio.wait_for(asyncio.shield(future), remaining)`, or an equivalent separate timer task, so a timeout does not cancel the response Future before the recommended answer is applied. On expiry it rechecks the current deadline—`stop_timer` may have cleared it—then atomically resolves the recommended option with `auto_submitted: true`.

`stop_timer` clears the server deadline while leaving the question pending and emits the updated question state. A reconnect receives the current pending question snapshot from the SSE manager; it does not create a second question or restart the 60 seconds.

Questions without automatic submission remain paused until answer, cancel, explicit turn stop, session deletion, SSE-manager eviction, or process shutdown. Every terminal path removes the registry entry and timer task.

`Ask User` is injected as a transient web capability for every direct chat, including existing persisted Assistants. Session tool advertisement must filter `supported_interfaces` before sending schemas to the model, so the transient capability can never leak into task, CLI, or programmatic prompts even if the same in-memory Agent object is reused. Execution already performs the corresponding interface check; schema filtering makes discovery and execution consistent.

## API and event contract

`POST /agents/question` is JWT-authenticated and owner-scoped. Its body is:

```json
{
  "request_id": "question-123",
  "action": "answer",
  "option_id": "background",
  "text": null
}
```

For `answer`, exactly one of a valid `option_id` or non-empty `text` is accepted. `cancel` and `stop_timer` accept neither. Repeated, foreign-owner, malformed, or already-resolved requests fail without mutating another turn.

The live SSE state is a `question_request` event containing the validated prompt, options, recommendation, `auto_submit_at`, and ids. Resolution uses the ordinary `tool_finished` event; `stop_timer` emits an updated `question_request` with the same request id and no deadline.

The tool result is structured JSON:

```json
{
  "status": "answered",
  "answer_type": "option",
  "option_id": "background",
  "text": "Run in background",
  "auto_submitted": false
}
```

Cancelled turns do not return a synthetic answer to the model.

## UI behavior

Render a reusable `QuestionCard` using the approval gate's visual language: compact mono type, `border-line`, `bg-panel-2`, outlined controls, semantic foreground colors, and 44px mobile targets. Use accent blue instead of the approval gate's danger red.

While active, one card is pinned immediately above the composer and receives focus without scrolling the transcript away. It contains:

- prompt and optional details;
- radio-style option rows with descriptions;
- a clearly marked `Recommended` badge;
- optional free-text input;
- primary Submit, secondary Cancel, and `Stop timer` when counting down;
- a calm progress bar and accessible text such as `Recommended answer in 42 seconds`.

Do not render a second live copy in the transcript. After resolution, remove the pinned card and reconstruct a compact read-only question card from the persisted `Ask User` tool call and tool result already present in session history. This avoids a second persistence model and keeps reload behavior consistent.

Use one one-second interval only while a deadline exists. Compute remaining time from `auto_submit_at`, clear the interval on resolution/unmount, and let the server response—not a local zero—declare the answer submitted.

## Failure behavior

- If no interactive web context exists, `Ask User` returns a clear unavailable-channel failure rather than hanging.
- Network failure while answering keeps the card active and shows a retryable inline error.
- A stale or already-resolved request refreshes pending state and removes the card if the server reports none.
- Stop-turn and Cancel both abort the waiting tool and mark running tool UI as stopped.
- Invalid recommendations, duplicate ids, oversized text, and auto-submit without a recommended option are rejected before emitting an event.

## Verification

Backend tests cover schema validation, owner isolation, one-active-question enforcement, manual option/text answers, cancellation, stop-timer, shielded automatic submission, cleanup, missing web context, and reconnect snapshots.

Frontend tests cover option and text submission, recommendation styling, absolute-deadline countdown, stop-timer, Cancel, retryable API errors, no duplicate live card, history reconstruction, focus behavior, and 320px layout.

Browser acceptance on `http://localhost:8000` must demonstrate a live question pausing and resuming the same Assistant turn, a 60-second recommended answer, stopping the timer, cancellation ending the turn, and a background choice causing an explicit `create_task` call.
