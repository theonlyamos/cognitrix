# Interactive Chat Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `Ask User` tool that pauses and resumes a live web-chat turn through an accessible inline question card, with optional safe 60-second recommended-answer submission.

**Architecture:** A focused in-process question broker owns validation, pending Futures, deadlines, owner checks, and cleanup. SSE supplies the live-turn context and reconnect snapshot; a JWT endpoint resolves the Future. The frontend keeps only pending state locally and derives completed cards from existing persisted tool calls/results.

**Tech Stack:** Python 3.11+, asyncio, FastAPI, SSE, React 18, TypeScript, Tailwind CSS, pytest, Vitest, Testing Library.

## Global Constraints

- Chat-only v1: do not expose `Ask User` to task workers, CLI, TUI, WebSocket, or blocking generate.
- One active question per live turn.
- Server time is authoritative; automatic submission is exactly 60 seconds and requires a recommended option.
- Automatic submission is limited to low-impact, reversible choices and never replaces approval.
- Cancel ends the current turn; it is not an answer returned to the model.
- Pending state is owner-scoped, single-use, in-process, and cleaned on every terminal path.
- Reconnect never duplicates a question or restarts its deadline.
- Completed cards derive from persisted tool history; do not add a second question persistence table.
- Preserve the unrelated edits in `frontend/src/components/final-accessibility.test.tsx` and `frontend/src/pages/TaskDetail.tsx`.

---

### Task 1: Define and test the question contract

**Files:**
- Create: `cognitrix/questions/__init__.py`
- Create: `cognitrix/questions/models.py`
- Create: `tests/test_question_models.py`

**Interfaces:**
- Produces: `QuestionOption`, `QuestionSpec`, `QuestionAnswer`, `QuestionAction`, and `QuestionValidationError`.
- Produces: `QuestionSpec.from_tool_args(...)` and JSON-safe event/result serializers.

- [ ] **Step 1: Write failing validation tests**

Cover empty/oversized prompts, zero response mechanisms, more than five options, duplicate ids, unknown recommendation ids, auto-submit without recommendation, and valid option/free-text specifications. Assert the exact fixed deadline duration is represented as 60 seconds rather than accepted from model input.

- [ ] **Step 2: Run the model tests and confirm the package is missing**

```powershell
python -m pytest tests/test_question_models.py -v
```

Expected: collection fails with `ModuleNotFoundError: cognitrix.questions`.

- [ ] **Step 3: Implement immutable validated dataclasses**

Use frozen dataclasses and a string enum for actions. Normalize surrounding whitespace, keep option ids stable, and expose only JSON primitives from serializers. Reject `auto_submit_recommended=True` unless `recommended_option_id` resolves to a declared option.

- [ ] **Step 4: Run tests and commit the contract**

```powershell
python -m pytest tests/test_question_models.py -v
git add -- cognitrix/questions/__init__.py cognitrix/questions/models.py tests/test_question_models.py
git diff --cached --check
git commit -m "feat(chat): define interactive question contract"
```

Expected: all model tests pass and the commit contains only the three listed paths.

---

### Task 2: Build the owner-scoped question broker

**Files:**
- Create: `cognitrix/questions/broker.py`
- Create: `tests/test_question_broker.py`

**Interfaces:**
- Produces: `question_turn_ctx: ContextVar[QuestionTurnContext | None]` and `QuestionTurnCancelled(asyncio.CancelledError)`.
- Produces: `ask_question(spec: QuestionSpec) -> QuestionAnswer`.
- Produces: `resolve_question(request_id, user_key, action, option_id=None, text=None) -> bool`.
- Produces: `pending_question(user_key, stream_id) -> dict | None` and `cancel_questions_for_stream(user_key, stream_id) -> None`.

- [ ] **Step 1: Write async broker tests**

Test event emission before waiting, owner isolation, option and free-text answers, double-resolution rejection, one-active-question rejection, unavailable-channel failure, explicit cancel raising the `asyncio.CancelledError` subtype `QuestionTurnCancelled`, and cleanup after each outcome.

- [ ] **Step 2: Add timer correctness tests with a short injected clock**

Assert the emitted event contains an absolute UTC deadline, `stop_timer` removes it without resolving the Future, reconnect snapshots preserve it, and expiry returns the recommended option with `auto_submitted=True`. Assert the Future remains usable at expiry by covering the shielded wait path. Cover the race where the old timeout fires after `stop_timer`: the broker must recheck the cleared deadline and continue waiting.

- [ ] **Step 3: Run tests and verify failures**

```powershell
python -m pytest tests/test_question_broker.py -v
```

Expected: collection fails because `cognitrix.questions.broker` does not exist.

- [ ] **Step 4: Implement the broker**

Store a `PendingQuestion` record keyed by request id plus a `(user_key, stream_id)` index. Generate opaque ids, emit the spec, and use `asyncio.wait_for(asyncio.shield(future), remaining)` for the deadline. On timeout, atomically resolve the recommendation. `stop_timer` clears the deadline and re-emits current state. Always remove indexes and timer tasks in `finally`.

- [ ] **Step 5: Run tests and commit the broker**

```powershell
python -m pytest tests/test_question_models.py tests/test_question_broker.py -v
git add -- cognitrix/questions/broker.py tests/test_question_broker.py
git diff --cached --check
git commit -m "feat(chat): add interactive question broker"
```

Expected: all question backend tests pass.

---

### Task 3: Expose Ask User only inside direct web chat

**Files:**
- Create: `cognitrix/tools/question.py`
- Modify: `cognitrix/tools/__init__.py`
- Modify: `cognitrix/models/tool.py:25-45`
- Modify: `cognitrix/tools/tool.py:17-75`
- Modify: `cognitrix/agents/base.py:485-518`
- Modify: `cognitrix/sessions/base.py:294-397`
- Modify: `cognitrix/utils/sse.py:699-745`
- Modify: `cognitrix/agents/templates.py`
- Create: `tests/test_ask_user_tool.py`
- Modify: `tests/test_sse_image_actions.py`
- Modify: `tests/test_context_engineering.py`

**Interfaces:**
- Consumes: `ask_question(QuestionSpec) -> QuestionAnswer` from Task 2.
- Produces: tool named `Ask User`, with `supported_interfaces=['web']`, `retryable=False`, and `max_attempts=1`.
- Produces: `Tool.occupies_execution_slot: bool = True`; `Ask User` sets it to `False`.
- Produces: Session schema filtering that advertises a tool only when its `supported_interfaces` contains the current interface.
- Produces: live SSE context and pending-question snapshot on reconnect.

- [ ] **Step 1: Write failing tool and exposure tests**

Assert valid arguments become a `QuestionSpec`, the structured answer is returned as JSON, invalid input is a tool failure, and cancellation propagates through `ToolBatchCancelled` to the existing Session cancellation path. Assert the tool is present for direct SSE chat but absent from task-worker, CLI, and programmatic tool schemas.

- [ ] **Step 2: Write failing SSE lifecycle tests**

Assert SSE binds `emit`, `session_id`, `stream_id`, and `user_key`; resets the context after success/error/cancel; emits a pending snapshot after reconnect; and calls broker cleanup when the current turn is stopped.

- [ ] **Step 3: Implement and register the tool**

Decorate `ask_user` as a non-retryable system tool restricted to the web interface. Its parameters mirror the design schema; it validates through `QuestionSpec`, awaits the broker, and returns serialized `QuestionAnswer` JSON. At the start of a web Session, append the registered tool if the Agent does not already contain it; this is transient runtime capability injection, not a database migration.

- [ ] **Step 4: Filter advertised schemas by interface**

Build `formatted_tools` only from tools whose `supported_interfaces` is empty or contains the current Session interface. Keep the existing execution-time check in `AgentManager.call_tools` as defense in depth. Add regression coverage using one reused Agent object: a web turn advertises `Ask User`, then a task turn on that object does not.

- [ ] **Step 5: Release the tool limiter during interactive waits**

Add `occupies_execution_slot: bool = True` to `Tool` and pass the decorator option through when constructing function tools. In `AgentManager.call_tools`, retain the current limiter around tools with the default value, but run tools with `False` without acquiring it. Mark only `Ask User` as `False`. Add a concurrency regression that parks four Ask User executions, then proves an ordinary tool can immediately acquire and complete through the shared limiter.

- [ ] **Step 6: Bind broker context and cancellation in SSE**

Set/reset `question_turn_ctx` beside `web_turn_ctx`. Include pending snapshot emission during connection setup. Route `QuestionTurnCancelled` through the same terminal state as an explicit user stop, and invoke stream cleanup from `stop_current_turn` and manager teardown.

- [ ] **Step 7: Add Assistant guidance**

Tell the default Assistant to ask only when an answer materially changes the result, offer background execution for genuinely long-running work, call `create_task(..., start_now=true)` only after the user chooses it, and restrict recommended auto-submit to low-impact reversible choices. Do not overwrite persisted custom prompts.

- [ ] **Step 8: Verify and commit the live tool**

```powershell
python -m pytest tests/test_ask_user_tool.py tests/test_sse_image_actions.py tests/test_context_engineering.py -v
git add -- cognitrix/tools/question.py cognitrix/tools/__init__.py cognitrix/models/tool.py cognitrix/tools/tool.py cognitrix/agents/base.py cognitrix/sessions/base.py cognitrix/utils/sse.py cognitrix/agents/templates.py tests/test_ask_user_tool.py tests/test_sse_image_actions.py tests/test_context_engineering.py
git diff --cached --check
git commit -m "feat(chat): pause turns with Ask User"
```

Expected: focused tests pass and task-worker tests prove the tool is unavailable there.

---

### Task 4: Add the independent question-response endpoint

**Files:**
- Modify: `cognitrix/api/routes/agents.py:438-467`
- Create: `tests/test_question_routes.py`

**Interfaces:**
- Consumes: `resolve_question(...)` and broker action enum from Task 2.
- Produces: `POST /agents/question` with `answer`, `cancel`, and `stop_timer` actions.

- [ ] **Step 1: Write route boundary tests**

Cover missing id/action, unknown action, option-plus-text conflict, fields on cancel/stop-timer, oversized text, valid option/text answers, owner forwarding, and stale/foreign requests returning 404. Assert the endpoint never queues onto the busy SSE action queue.

- [ ] **Step 2: Run tests and verify the route is absent**

```powershell
python -m pytest tests/test_question_routes.py -v
```

Expected: 404 or missing endpoint failures.

- [ ] **Step 3: Implement strict request parsing**

Authenticate with `jwt_only`, derive `user_key` from the current user, normalize through the question contract, and call the broker directly. Return `{status: 'resolved'}` for answer/cancel and `{status: 'timer_stopped'}` for stop-timer. Return 404 for foreign, stale, or already-resolved ids.

- [ ] **Step 4: Verify and commit the endpoint**

```powershell
python -m pytest tests/test_question_routes.py tests/test_question_broker.py -v
git add -- cognitrix/api/routes/agents.py tests/test_question_routes.py
git diff --cached --check
git commit -m "feat(api): resolve live chat questions"
```

Expected: all route and broker tests pass.

---

### Task 5: Build the reusable themed QuestionCard

**Files:**
- Create: `frontend/src/components/QuestionCard.tsx`
- Create: `frontend/src/components/QuestionCard.test.tsx`

**Interfaces:**
- Produces: `QuestionRequest`, `QuestionResolution`, and `QuestionCard` props for active/read-only modes.
- Produces: callbacks `onAnswer`, `onCancel`, and `onStopTimer`.

- [ ] **Step 1: Write component tests**

Cover semantic group/labels, recommendation badge, option and free-text exclusivity, disabled Submit until valid, 44px controls, absolute-deadline display, accessible countdown text, Stop timer visibility, read-only resolution, inline API error, and focus moving to the question heading.

- [ ] **Step 2: Run tests and verify the component is missing**

```powershell
cd frontend
pnpm vitest run src/components/QuestionCard.test.tsx
```

Expected: import failure for `QuestionCard`.

- [ ] **Step 3: Implement the approval-themed card**

Use the approval gate tokens (`border-line`, `bg-panel-2`, compact mono labels, outlined controls) with accent semantics. Derive remaining seconds from `Date.parse(auto_submit_at) - Date.now()` using one interval only while active. Never locally resolve at zero; show `Submitting recommended answer…` until the server event/response resolves it.

- [ ] **Step 4: Verify and commit the component**

```powershell
pnpm vitest run src/components/QuestionCard.test.tsx
cd ..
git add -- frontend/src/components/QuestionCard.tsx frontend/src/components/QuestionCard.test.tsx
git diff --cached --check
git commit -m "feat(ui): add interactive question card"
```

Expected: component tests pass.

---

### Task 6: Integrate pending and historical questions into Home

**Files:**
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/context/SessionContext.tsx`
- Modify: `frontend/src/lib/transcript.ts`
- Modify: `frontend/src/components/TranscriptView.tsx`
- Create: `frontend/src/pages/Home.questions.test.tsx`
- Modify: `frontend/src/components/TranscriptView.test.tsx`

**Interfaces:**
- Consumes: SSE `question_request`, `POST /agents/question`, and `QuestionCard` from Tasks 4-5.
- Produces: exactly one pinned active card and compact completed cards derived from Ask User tool history.

- [ ] **Step 1: Write failing live-flow tests**

Assert `question_request` pins one card above the composer; option/text answers call the endpoint; network errors keep it active; stop-timer updates the same card; Cancel ends the turn; duplicate/replayed request ids do not duplicate DOM; and a resolved event removes the pinned card.

- [ ] **Step 2: Write failing history tests**

Feed transcript parsing an `Ask User` tool call plus structured result and assert one read-only question entry is produced. Assert a still-running call is not rendered in history while its pinned card is active.

- [ ] **Step 3: Add pending question state to SessionContext**

Store a single `QuestionRequest | null`, replace it idempotently by request id, and clear it on resolution, stop, session change, and terminal failure. Do not persist it to local storage.

- [ ] **Step 4: Wire Home events and actions**

Handle `question_request`, render `QuestionCard` immediately above the composer, and post direct authenticated actions to `/agents/question`. Keep the draft composer usable for reading but prevent starting a second turn while the question is pending. On retryable errors, show the error inside the same card.

- [ ] **Step 5: Parse resolved history cards**

Add a `question` transcript entry derived only from the `Ask User` call arguments and its paired structured tool result. Render it through read-only `QuestionCard` in `TranscriptView`; do not create a new session message type or database row.

- [ ] **Step 6: Verify frontend integration and build**

```powershell
cd frontend
pnpm vitest run src/components/QuestionCard.test.tsx src/pages/Home.questions.test.tsx src/components/TranscriptView.test.tsx
pnpm build
cd ..
git add -- frontend/src/components/QuestionCard.tsx frontend/src/pages/Home.tsx frontend/src/context/SessionContext.tsx frontend/src/lib/transcript.ts frontend/src/components/TranscriptView.tsx frontend/src/pages/Home.questions.test.tsx frontend/src/components/TranscriptView.test.tsx
git diff --cached --check
git commit -m "feat(chat): render live and resolved questions"
```

Expected: focused tests and production build pass; unrelated dirty frontend tests/pages remain unstaged.

---

### Task 7: Verify end-to-end pause, resume, timer, and background handoff

**Files:**
- Verify only: all paths changed in Tasks 1-6.

**Interfaces:**
- Produces: automated and browser evidence for the complete question lifecycle.

- [ ] **Step 1: Run focused backend verification**

```powershell
python -m pytest tests/test_question_models.py tests/test_question_broker.py tests/test_ask_user_tool.py tests/test_question_routes.py tests/test_sse_image_actions.py tests/test_context_engineering.py -v
python -m py_compile cognitrix/questions/models.py cognitrix/questions/broker.py cognitrix/tools/question.py cognitrix/api/routes/agents.py cognitrix/utils/sse.py
```

Expected: all selected tests pass and compilation is silent.

- [ ] **Step 2: Run focused frontend verification**

```powershell
cd frontend
pnpm vitest run src/components/QuestionCard.test.tsx src/pages/Home.questions.test.tsx src/components/TranscriptView.test.tsx
pnpm build
cd ..
git diff --check
```

Expected: tests/build pass and diff check reports no whitespace errors.

- [ ] **Step 3: Test manual pause and resume on localhost**

At `http://localhost:8000`, ask the Assistant for work with a material ambiguity. Confirm one themed question card appears, choose an option, and verify the same turn resumes and completes. Reload while paused and verify the same card/deadline returns once.

- [ ] **Step 4: Test text, timer, stop, and cancel**

Exercise free text, a recommended option with 60-second auto-submit, Stop timer followed by a delayed manual answer, and Cancel. Confirm Cancel ends the turn and no model output follows it.

- [ ] **Step 5: Test background handoff and mobile layout**

Ask for genuinely long-running work, choose Background, and verify the resumed Assistant explicitly calls `Create Task` with `start_now=true`. At 320px, confirm the card and composer do not overlap or overflow and every control remains at least 44px high.

- [ ] **Step 6: Audit final scope**

```powershell
git status --short
git diff --check
```

Expected: no unplanned staged files and the user's existing `TaskDetail.tsx` and accessibility-test edits remain intact.
