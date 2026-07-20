# Explicit Task Intent Routing

## Goal

Keep ordinary chat requests in the interactive agent loop. A chat message becomes a persisted, orchestrated task only when the user explicitly asks for task creation or task execution. Requests to create or manage other resources, such as teams and agents, must remain ordinary chat tool calls.

## Routing contract

Replace the current verb-count heuristic in `cognitrix/tasks/handler.py` with a strict explicit-intent predicate. The predicate returns true only when the message clearly requests the task abstraction, using phrases such as:

- `create a task`, `make a task`, or `add a task`;
- `run this as a task` or `turn this into a task`;
- `start a background task` or `schedule a task`;
- an equivalent command where `task` is the requested persisted object.

Numbered steps, sequencing words, complexity, or multiple action verbs are not sufficient. For example, `research vendors, then write a report` stays in chat, while `create a task to research vendors and write a report` enters the task orchestrator.

The predicate remains centralized so SSE, WebSocket, TUI, and shell callers share one policy. Attachments and document capabilities retain their existing direct-chat behavior.

## Assistant behavior

Add a defense-in-depth instruction to the default Assistant template: complete requests directly with ordinary tools, and create a persisted task only when the user explicitly asks for one. Routing remains authoritative because it executes before the model receives the prompt.

Changing the template affects newly created default assistants. Existing persisted assistants must not be silently overwritten because users may have customized their prompts. Updating an existing Assistant is a separate, explicit data operation.

## Authorization and errors

Direct chat tool calls continue under the authenticated turn's tool execution context. This allows management tools such as `Create New Team` to enforce their existing `write` scope and agent/team allowlists without passing through least-privilege durable task authority.

If a user explicitly requests a task that contains management operations, the task may still fail when its durable authority lacks `write`; that is a separate authority-propagation concern and is not broadened by this change.

## Verification

Add focused routing tests covering:

- ordinary multi-action prompts remain chat;
- resource-management requests containing words such as `create` and `research` remain chat;
- explicit task-creation and task-execution phrases route to the orchestrator;
- case and surrounding punctuation do not change the result;
- the Assistant template contains the matching explicit-task rule.

Run the focused planning/routing and context-engineering tests, then perform an in-app browser check: ask chat to create a team and verify that the conversation shows `Create New Team` directly, no task is created, and the team appears on the Teams page.
