# Audit Fixes Design

## Scope

Resolve the six findings from the uncommitted-changes audit without altering
unrelated behavior: agent delegation authorization, OpenRouter defaults, tool
error status, historical preview size, total tool-call limits, and tool-chip
accessibility.

## Agent delegation boundary

Agent-management tools receive the invoking agent as runtime-only context.
`create_agent` creates a child whose `parent_id` is the caller's ID.
`list_agents` returns only that caller's children, and `call_agent` resolves a
target only when its `parent_id` matches the caller. Missing caller context is
treated as an error rather than falling back to a global agent query.

Runtime-injected arguments are added to a copy of the model-supplied arguments.
They must not mutate the tool call stored in conversation history.

This deliberately removes root-agent-to-root-agent delegation. Delegation is
limited to explicitly created child agents, matching the existing sub-agent
terminology and preventing API-key allowlist bypasses through nested calls.

## Tool status and history

Every entry returned by `call_tools` includes explicit success metadata.
Malformed calls, missing tools, denied approvals, raised exceptions, and failed
tool results are unsuccessful. The session emits `error` for unsuccessful live
tool events and `completed` only for successful calls.

The success state is also persisted on tool-result history entries. Transcript
restoration maps it to `done` or `error`, so live and restored conversations
render consistently.

## Preview limits

Historical tool arguments and results are capped to 4,000 characters before
they enter chat-message state. Truncation uses the same total-length message as
the live backend preview. Full persisted history remains available to the agent;
the cap applies only to browser display data.

## Execution budget

Keep the existing 100-round default and add a distinct total tool-call budget:
`COGNITRIX_MAX_TOOL_CALLS`, defaulting to 100. Before accepting a batch, the
session verifies that the full batch fits within the remaining budget. If it
does not, the turn stops with a user-visible limit message before persisting an
unanswered assistant tool-call entry.

## UI accessibility

Tool summaries retain their visible status icons and add screen-reader text for
running, completed, and failed states. Native `details` and `summary` keyboard
behavior remains unchanged.

## Provider default

The OpenRouter default model becomes `tencent/hy3:free` everywhere the provider
default is defined. Google-specific defaults remain unchanged.

## Testing and verification

Regression tests are written before implementation and observed failing for:

- caller-scoped agent listing and delegation;
- unsuccessful live and restored tool status;
- total tool-call budget enforcement;
- historical preview truncation.

After each minimal fix, the relevant test is rerun to green. Final verification
runs the affected backend test suite, frontend TypeScript compilation, and a
working-tree diff check. The repository's existing ESLint configuration gap is
reported separately if it still prevents lint execution.
