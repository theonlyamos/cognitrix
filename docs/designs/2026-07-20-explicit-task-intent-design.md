# Explicit Chat Execution Mode

## Goal

Make the caller, rather than keyword inference, choose whether a web chat submission is an ordinary agent turn or a persisted orchestrated task. Team, agent, and other resource-management requests sent normally must remain direct chat tool calls.

## Transport contract

`POST /agents/chat` accepts an `execution_mode` field with exactly two values:

- `chat`: run the prompt through the current interactive Session and its direct tool loop;
- `task`: persist the prompt as a Task and run it through the task orchestrator.

The field defaults to `chat` when omitted so existing clients remain safe and backward compatible. Any other value returns HTTP 400 before beginning a turn or staging uploads. The normalized mode is copied into the SSE action queue; the consumer branches only on that value and never examines prompt wording.

Task mode does not support attachments, edit-source descriptors, or document capability ids because the orchestrator does not currently adopt those session-scoped capabilities. The API returns HTTP 400 for those combinations instead of silently changing modes or orphaning uploaded data.

The existing WebSocket message types already express the same choice: `generate` means chat and `multistep` means task. Remove the secondary keyword check from the `multistep` branch so the selected message type is authoritative. The blocking `/{agent_id}/generate` endpoint remains direct chat. CLI and TUI keyword routing remain unchanged and are outside this web UI/API change.

## UI behavior

Normal Send, the send-arrow button, and Enter always submit `execution_mode: "chat"`. Add a separate `Run as task` action near the composer controls that submits the same prompt with `execution_mode: "task"`.

The task action is non-sticky: each click applies only to that submission, eliminating accidental task creation on later messages. It uses the same busy and connection guards as Send and is disabled when attachments or an edit source are present. Its title explains why it is unavailable. To protect the compact mobile composer, the footer may wrap and keyboard-hint text is hidden at narrow widths.

## Assistant behavior

Add a defense-in-depth instruction to the default Assistant template: complete requests directly with ordinary tools, and create a persisted task through management tools only when the user explicitly requests that object. This rule controls the Assistant's own `create_task` tool use; it does not select the transport execution mode.

Changing the template affects newly created default assistants. Existing persisted assistants must not be silently overwritten because users may have customized their prompts.

## Authorization and errors

Direct chat retains the authenticated turn's `write` scope and allowlists, allowing management tools such as `Create New Team` to work without durable task authority. Task mode retains the existing orchestrator authority behavior; management tools inside a durable task may still fail without `write`, which is a separate authority-propagation concern.

The API rejects invalid modes and unsupported task inputs before `begin_turn()`. Frontend submission failures restore the draft and attachments using the existing recovery behavior.

## Verification

Automated coverage must prove:

- omitted mode normalizes to `chat` in JSON and multipart requests;
- explicit `task` reaches the queue unchanged;
- invalid modes and task-plus-attachment combinations return HTTP 400 without beginning a turn;
- SSE chooses direct Session or orchestrator solely from `execution_mode`;
- WebSocket `generate` and `multistep` types are authoritative;
- Send/Enter posts `chat`, while `Run as task` posts `task` and remains accessible on mobile;
- the default Assistant template contains the matching management-tool rule.

Browser acceptance on `http://localhost:8000` must show that creating a team with normal Send calls `Create New Team` directly and creates no task. A separate prompt submitted with `Run as task` must surface a task-page link. The task action must remain usable at 320px without hiding or covering the composer.
