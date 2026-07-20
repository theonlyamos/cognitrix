# Explicit Chat Execution Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make web callers explicitly select direct chat or persisted task execution without inspecting prompt wording.

**Architecture:** Normalize `execution_mode` at the HTTP boundary, carry it unchanged through the SSE action queue, and branch on it in the consumer. Normal Send/Enter always use `chat`; a separate non-sticky `Run as task` action uses `task`. Existing WebSocket `generate` and `multistep` message types remain the equivalent explicit contract.

**Tech Stack:** Python 3.11+, FastAPI, asyncio/SSE, React 18, TypeScript, Tailwind CSS, pytest, Vitest, Testing Library.

## Global Constraints

- `execution_mode` accepts exactly `chat` and `task`; omission defaults to `chat`.
- Prompt text never selects web execution mode.
- Invalid modes and task submissions with attachments, edit-source descriptors, or document ids fail with HTTP 400 before `begin_turn()`.
- Send, the send arrow, and Enter always use `chat`; `Run as task` applies to one submission only.
- The new task action must remain accessible and visible at 320px.
- WebSocket `generate` means chat and `multistep` means task without keyword rechecking.
- The blocking `/{agent_id}/generate`, CLI, and TUI behavior remain unchanged.
- Existing persisted Assistant prompts are not overwritten.
- Do not stage or modify the unrelated working-tree changes in `frontend/src/components/final-accessibility.test.tsx` or `frontend/src/pages/TaskDetail.tsx`.

---

### Task 1: Define and validate the HTTP execution-mode contract

**Files:**
- Create: `cognitrix/tasks/execution_mode.py`
- Create: `tests/test_execution_mode.py`
- Modify: `cognitrix/api/routes/agents.py:351-401`
- Modify: `tests/test_agent_image_uploads.py:178-207`

**Interfaces:**
- Produces: `ExecutionMode(StrEnum)` with `CHAT` and `TASK`; `parse_execution_mode(value: object) -> ExecutionMode`.
- Produces: `/agents/chat` queue actions with `execution_mode: "chat" | "task"`.

- [ ] **Step 1: Write failing normalization tests**

Create `tests/test_execution_mode.py`:

```python
import pytest

from cognitrix.tasks.execution_mode import ExecutionMode, parse_execution_mode


def test_execution_mode_defaults_to_chat():
    assert parse_execution_mode(None) is ExecutionMode.CHAT


@pytest.mark.parametrize("value", ["chat", ExecutionMode.CHAT])
def test_execution_mode_accepts_chat(value):
    assert parse_execution_mode(value) is ExecutionMode.CHAT


@pytest.mark.parametrize("value", ["task", ExecutionMode.TASK])
def test_execution_mode_accepts_task(value):
    assert parse_execution_mode(value) is ExecutionMode.TASK


@pytest.mark.parametrize("value", ["auto", "", 1, True])
def test_execution_mode_rejects_unknown_values(value):
    with pytest.raises(ValueError, match="execution_mode must be 'chat' or 'task'"):
        parse_execution_mode(value)
```

- [ ] **Step 2: Run the new tests and verify the module is missing**

```powershell
python -m pytest tests/test_execution_mode.py -v
```

Expected: collection fails with `ModuleNotFoundError: cognitrix.tasks.execution_mode`.

- [ ] **Step 3: Implement the minimal execution-mode type**

Create `cognitrix/tasks/execution_mode.py`:

```python
from enum import StrEnum


class ExecutionMode(StrEnum):
    CHAT = "chat"
    TASK = "task"


def parse_execution_mode(value: object) -> ExecutionMode:
    if value is None:
        return ExecutionMode.CHAT
    try:
        return ExecutionMode(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("execution_mode must be 'chat' or 'task'") from exc
```

- [ ] **Step 4: Add failing `/agents/chat` boundary tests**

Extend `tests/test_agent_image_uploads.py` so the existing JSON queue assertion includes:

```python
'execution_mode': 'chat',
```

Add:

```python
@pytest.mark.asyncio
async def test_chat_endpoint_preserves_explicit_task_mode(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    await agent_routes.chat_endpoint(
        JsonRequest({
            'agent_id': agent.id,
            'stream_id': 'browser-a',
            'message': 'prepare the report',
            'execution_mode': 'task',
        }),
        user=types.SimpleNamespace(id='user-a'),
    )
    assert manager.action_queue.actions[0]['execution_mode'] == 'task'


@pytest.mark.asyncio
async def test_chat_endpoint_rejects_invalid_execution_mode_before_turn(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(
            JsonRequest({
                'agent_id': agent.id,
                'stream_id': 'browser-a',
                'message': 'hello',
                'execution_mode': 'auto',
            }),
            user=types.SimpleNamespace(id='user-a'),
        )
    assert exc.value.status_code == 400
    assert manager.begin_calls == 0


@pytest.mark.asyncio
async def test_chat_endpoint_rejects_task_mode_attachments_before_turn(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {
            'agent_id': agent.id,
            'stream_id': 'browser-a',
            'message': 'inspect this',
            'execution_mode': 'task',
        },
        [('file.txt', b'content', 'text/plain')],
    )
    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))
    assert exc.value.status_code == 400
    assert manager.begin_calls == 0
```

- [ ] **Step 5: Validate and enqueue the normalized mode before side effects**

In `chat_endpoint`, parse immediately after `_chat_request_parts` opens:

```python
try:
    execution_mode = parse_execution_mode(data.get('execution_mode'))
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc

has_task_incompatible_input = bool(
    upload_files
    or data.get('attachments')
    or data.get('document_ids')
    or data.get('edit_source_artifact_id') is not None
    or data.get('edit_source_image_index') is not None
)
if execution_mode is ExecutionMode.TASK and has_task_incompatible_input:
    raise HTTPException(
        status_code=400,
        detail='Task execution mode does not support attachments or document capabilities',
    )
```

Add `execution_mode.value` to the queued action. Import `ExecutionMode` and `parse_execution_mode` from `cognitrix.tasks.execution_mode`.

- [ ] **Step 6: Run and commit the transport contract**

```powershell
python -m pytest tests/test_execution_mode.py tests/test_agent_image_uploads.py -v
git add -- cognitrix/tasks/execution_mode.py cognitrix/api/routes/agents.py tests/test_execution_mode.py tests/test_agent_image_uploads.py
git diff --cached --check
git commit -m "feat(chat): add explicit execution mode"
```

Expected: all selected tests pass; the commit contains exactly the four listed files.

---

### Task 2: Make SSE and WebSocket routing mode-driven

**Files:**
- Modify: `cognitrix/utils/sse.py:36,660-699`
- Modify: `cognitrix/utils/ws.py:34,432-479`
- Modify: `tests/test_sse_image_actions.py:1120-1160`
- Modify: `tests/test_session_ownership_ws.py`

**Interfaces:**
- Consumes: `action['execution_mode']` from Task 1.
- Produces: SSE `chat` -> Session, SSE `task` -> `handle_multi_step_task`; WebSocket `generate` -> Session, `multistep` -> orchestrator.

- [ ] **Step 1: Replace the attachment heuristic regression with a mode regression**

Update the existing SSE test to enqueue an attachment action with omitted mode and assert `handle_multi_step_task` is never called:

```python
async def forbidden_task_mode(*_args, **_kwargs):
    pytest.fail('Omitted execution_mode must default to direct chat')

monkeypatch.setattr(sse, 'handle_multi_step_task', forbidden_task_mode)
await manager.action_queue.put({
    'type': 'chat_message',
    'content': 'first do this, then do that',
    'session_id': 'session-1',
    'staged_attachments': staged,
    'execution_mode': 'chat',
})
```

Add a focused SSE test using a fake `handle_multi_step_task` that records the prompt and returns a result for an action containing `execution_mode: 'task'`; assert the emitted terminal event is `multistep_result` and the fake Session is not invoked.

- [ ] **Step 2: Run the focused SSE tests and verify keyword routing still interferes**

```powershell
python -m pytest tests/test_sse_image_actions.py -k "execution_mode or multistep_prompt" -v
```

Expected: at least one failure because SSE still calls `is_multi_step_task(user_prompt)`.

- [ ] **Step 3: Replace SSE keyword inspection with the queued mode**

In `cognitrix/utils/sse.py`, remove the `is_multi_step_task` import and change the branch to:

```python
execution_mode = action.get('execution_mode', 'chat')
if execution_mode == 'task':
    await emit({'type': 'status', 'content': 'Planning task...'})
    # Keep the existing notify_task and handle_multi_step_task body unchanged.
else:
    # Keep the existing Session path unchanged.
```

Do not retain prompt-based fallbacks. The HTTP boundary is responsible for rejecting unsupported task inputs.

- [ ] **Step 4: Add a WebSocket regression that forbids keyword classification**

Extend the existing WebSocket test harness so a `type: 'multistep'` message with prompt `hello` reaches a stubbed `handle_multi_step_task`. Patch `is_multi_step_task` to raise `AssertionError` if invoked and assert the socket receives `multistep_result`.

- [ ] **Step 5: Make WebSocket message type authoritative**

In the `query_type == 'multistep'` branch, remove `if is_multi_step_task(prompt)` and its direct-Session `else`. Always send planning status, call `handle_multi_step_task`, and emit `multistep_result`. Remove the now-unused `is_multi_step_task` import from `cognitrix/utils/ws.py`. Leave `query_type == 'generate'` unchanged.

- [ ] **Step 6: Run and commit runtime routing**

```powershell
python -m pytest tests/test_sse_image_actions.py tests/test_session_ownership_ws.py -v
git add -- cognitrix/utils/sse.py cognitrix/utils/ws.py tests/test_sse_image_actions.py tests/test_session_ownership_ws.py
git diff --cached --check
git commit -m "fix(chat): route web turns by execution mode"
```

Expected: all selected tests pass; no prompt classifier remains in SSE or WebSocket web routing.

---

### Task 3: Add the non-sticky Run as task UI action

**Files:**
- Modify: `frontend/src/pages/Home.tsx:521-585,1000-1077`
- Create: `frontend/src/pages/Home.execution-mode.test.tsx`

**Interfaces:**
- Consumes: `/agents/chat` contract from Task 1.
- Produces: `send(text: string, executionMode?: 'chat' | 'task')`; normal actions post `chat`, task action posts `task`.

- [ ] **Step 1: Create focused UI transport tests**

Create `Home.execution-mode.test.tsx` using the same `useSession`, `useResource`, `useSSE`, and API mocks as `Home.image-editing.test.tsx`, then add:

```tsx
it('uses chat mode for Enter and the primary Send action', async () => {
  const user = userEvent.setup();
  renderHome();
  const input = screen.getByRole('combobox', { name: 'Message the agent' });
  await user.type(input, 'hello{enter}');
  await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat',
    expect.objectContaining({ message: 'hello', execution_mode: 'chat' })));
});

it('uses task mode only for the Run as task action', async () => {
  const user = userEvent.setup();
  renderHome();
  const input = screen.getByRole('combobox', { name: 'Message the agent' });
  await user.type(input, 'prepare a report');
  await user.click(screen.getByRole('button', { name: 'Run as task' }));
  await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agents/chat',
    expect.objectContaining({ message: 'prepare a report', execution_mode: 'task' })));
});

it('disables task mode when an attachment is selected', async () => {
  renderHome();
  fireEvent.change(document.querySelector('input[type="file"]')!, {
    target: { files: [new File(['x'], 'note.txt', { type: 'text/plain' })] },
  });
  expect(await screen.findByRole('button', { name: 'Run as task' })).toBeDisabled();
});
```

- [ ] **Step 2: Run the new UI tests and verify the action is missing**

```powershell
cd frontend
pnpm vitest run src/pages/Home.execution-mode.test.tsx
```

Expected: tests fail because `Run as task` and `execution_mode` do not exist.

- [ ] **Step 3: Pass execution mode through the existing send function**

Change the callback signature and payload:

```tsx
type ExecutionMode = 'chat' | 'task';

const send = useCallback(
  async (text: string, executionMode: ExecutionMode = 'chat') => {
    // Existing guards and draft handling stay unchanged.
    const payload = {
      message: msg,
      execution_mode: executionMode,
      // Existing conditional payload fields stay unchanged.
    };
```

Keep Enter and the primary button as `send(input)` so they default to chat.

- [ ] **Step 4: Add a dedicated task action without sticky state**

Add this footer action after auto-approve:

```tsx
<button
  type="button"
  onClick={() => void send(input, 'task')}
  disabled={busy || !!uploadError || !isConnected || !input.trim()
    || attachments.length > 0 || editSource !== null}
  aria-label="Run as task"
  title={attachments.length > 0 || editSource !== null
    ? 'Task mode does not support attachments or edit sources yet.'
    : 'Create a persisted task and run it with step tracking.'}
  className="flex min-h-11 items-center gap-1 transition-colors hover:text-accent disabled:cursor-not-allowed disabled:opacity-50 md:min-h-0"
>
  <span aria-hidden>[ ]</span> run as task
</button>
```

Add `flex-wrap` to the footer controls. Hide the two keyboard-hint spans below the `sm` breakpoint so the new action remains visible at 320px; do not change the textarea or primary Send touch target.

- [ ] **Step 5: Run frontend verification and commit the UI**

```powershell
cd frontend
pnpm vitest run src/pages/Home.execution-mode.test.tsx src/pages/Home.image-editing.test.tsx
pnpm build
cd ..
git add -- frontend/src/pages/Home.tsx frontend/src/pages/Home.execution-mode.test.tsx
git diff --cached --check
git commit -m "feat(chat): add explicit task action"
```

Expected: selected tests and build pass; the commit does not include the pre-existing accessibility or TaskDetail edits.

---

### Task 4: Align the default Assistant management-tool rule

**Files:**
- Modify: `cognitrix/agents/templates.py:4-12`
- Modify: `tests/test_context_engineering.py:61-64`

**Interfaces:**
- Produces: a default Assistant prompt rule governing its own persisted-task management tool calls, independent of transport mode.

- [ ] **Step 1: Add a failing prompt-contract test**

```python
def test_system_prompt_requires_explicit_persisted_task_request():
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT

    prompt = ASSISTANT_SYSTEM_PROMPT.lower()
    assert "complete requests directly with ordinary tools" in prompt
    assert "create a persisted task only when the user explicitly asks" in prompt
```

- [ ] **Step 2: Add the two matching instructions**

Under `## Instructions`, add:

```text
- Complete requests directly with ordinary tools.
- Create a persisted task only when the user explicitly asks for one.
```

Replace the broader `Complete tasks directly and autonomously` sentence to avoid duplication. Do not mutate existing Agent database rows.

- [ ] **Step 3: Verify and commit the prompt contract**

```powershell
python -m pytest tests/test_context_engineering.py -v
git add -- cognitrix/agents/templates.py tests/test_context_engineering.py
git diff --cached --check
git commit -m "fix(agent): clarify persisted task creation"
```

Expected: context-engineering tests pass; the commit contains exactly two files.

---

### Task 5: Verify the end-to-end mode boundary

**Files:**
- Verify only: all files changed in Tasks 1-4.

**Interfaces:**
- Produces: automated and live-browser evidence for both explicit modes.

- [ ] **Step 1: Run focused backend and frontend verification**

```powershell
python -m pytest tests/test_execution_mode.py tests/test_agent_image_uploads.py tests/test_sse_image_actions.py tests/test_session_ownership_ws.py tests/test_context_engineering.py -v
python -m py_compile cognitrix/tasks/execution_mode.py cognitrix/api/routes/agents.py cognitrix/utils/sse.py cognitrix/utils/ws.py cognitrix/agents/templates.py
cd frontend
pnpm vitest run src/pages/Home.execution-mode.test.tsx src/pages/Home.image-editing.test.tsx
pnpm build
cd ..
git diff --check
```

Expected: all selected tests pass, compilation succeeds silently, build succeeds, and diff check reports no errors.

- [ ] **Step 2: Reload the existing localhost application**

Use `http://localhost:8000`. Restart the backend only if it does not reload Python changes automatically; do not start the separate frontend dev server unless port 8000 cannot serve the rebuilt frontend.

- [ ] **Step 3: Verify direct team creation with normal Send**

Send normally:

```text
Create a team named Product Research Team for investigating software products and market trends. Add Assistant as its member and leader.
```

Expected: `Create New Team` runs directly in chat, no `Task created` link appears, no new Task row is created, and the team appears on `/teams`.

- [ ] **Step 4: Verify explicit task execution and mobile layout**

In a new chat, enter `Report the current date` and click `Run as task`.

Expected: a task-page link appears and the run reaches a visible terminal state. At a 320px viewport, the task action and composer remain visible without horizontal overflow or overlap.

- [ ] **Step 5: Audit final scope**

```powershell
git status --short
git diff --check
```

Expected: unrelated frontend modifications remain untouched and no unplanned file is staged.
