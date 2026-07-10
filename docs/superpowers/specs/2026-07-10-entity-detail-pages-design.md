# Agent and Team Detail Pages Design

## Goal

Give agents and teams the same list-to-detail navigation model already used by tasks. Keep creation and editing in dedicated form routes, and place entity-level actions in the read-only detail header.

## Route Contract

| Route | Purpose |
| --- | --- |
| `/agents` | Agent list. Each row links to `/agents/:agentId`. |
| `/agents/new` | Existing agent creation form. |
| `/agents/:agentId` | New read-only agent detail page. |
| `/agents/:agentId/edit` | Existing agent form in edit mode. |
| `/teams` | Team list. Each row links to `/teams/:teamId`. |
| `/teams/new` | Existing team creation form. |
| `/teams/:teamId` | Canonical read-only team detail page. |
| `/teams/:teamId/edit` | Existing team form in edit mode. |

The `/teams/:teamId/interact` route and its nested interaction variants are removed. The current team interaction overview becomes the team detail page because it already presents the team description, members, leader, and related tasks.

## Agent Detail Page

The agent detail page loads `/agents/:agentId` and follows the task-detail header pattern:

- Back control to `/agents`.
- `AGENT` eyebrow and agent name.
- `Interact` primary action.
- `Edit` action linking to `/agents/:agentId/edit`.
- `Delete` danger action with the existing confirmation text. A successful deletion returns to `/agents`; failures render an inline error without navigating.

The read-only content shows:

- Provider, model, temperature, and maximum tokens when configured.
- System prompt, with a clear empty value when none is configured.
- Configured tools as a count and named list.
- Base URL when configured.
- Short agent identifier for support/debugging context.
- Tasks currently assigned to the agent, with links to their detail pages.

### Agent Interact Handoff

The Interact action reuses Home's existing persisted selection contract before navigating to `/home`:

1. Set `selectedAgentId` to the chosen agent ID.
2. Set `chatSession:<agentId>` to the empty-string sentinel that Home already interprets as a deliberate blank conversation.
3. Navigate to `/home`.

This guarantees the chosen agent is selected and no prior conversation is restored. It avoids adding a second query-parameter or router-state synchronization path to Home.

## Team Detail Page

Rename the current `TeamInteraction` page to `TeamDetail` and serve it from `/teams/:teamId`. Its header contains:

- Back control to `/teams`.
- `TEAM` eyebrow and team name.
- `Edit` action linking to `/teams/:teamId/edit`.
- `Delete` danger action with confirmation. A successful deletion returns to `/teams`; failures render an inline error without navigating.

The existing overview remains the detail content:

- Description and team identifier.
- Member, task, and leader context.
- Members linking to `/agents/:agentId`.
- Related tasks linking to `/tasks/:taskId`.
- Empty-state links for adding members through `/teams/:teamId/edit`.
- New-task action remains available.

There is no separate team Interact action or route.

## Task Assignment From Details

Both detail pages include an `Assign task` action beside their task section and a `New task` action. These cover existing and new work without introducing a second task model. Existing-task ownership changes use a narrow assignment endpoint so execution and schedule fields never participate in the update.

### Assign an Existing Task

`Assign task` expands an inline picker containing tasks that are not already assigned to the current entity. The picker supports selecting one or more tasks, then applying the assignment in one explicit action. While updates are running, its controls are disabled and an accessible status is announced. A failed update leaves the picker open, preserves the user's selection, and renders an inline error. Successful updates refresh the visible task section and close the picker.

Assignments preserve the task model's existing rules:

- Agent detail: append the agent ID to each task's `assigned_agents` list, preserving other assigned agents and any existing `team_id`.
- Team detail: set each task's `team_id` to the team ID and set `assigned_agents` to the team's current member IDs, matching `TaskPage.changeTeam` semantics. Reassigning a task from another team requires no extra confirmation because the picker and final `Assign selected` action make the destination explicit.
- Completed, failed, cancelled, scheduled, and running tasks remain eligible; assigning changes ownership only and does not change task status, run history, schedule, steps, or results.

The frontend sends only `assigned_agents` and `team_id` to `PATCH /tasks/:taskId/assignment`. The endpoint applies a partial database update and returns the refreshed task without invoking autostart or schedule logic. Multiple selections settle independently: successful updates are refreshed immediately, only failed tasks remain selected, and the error identifies partial failure instead of presenting it as a total rollback.

### Create a Preassigned Task

The `New task` action links to:

- `/tasks/new?agentId=<agentId>` from agent detail.
- `/tasks/new?teamId=<teamId>` from team detail.

`TaskPage` reads these parameters only in create mode. After agent data loads, a valid `agentId` is added to the initial selected-agent set. After team data loads, a valid `teamId` selects the team and pre-fills its current members using the same logic as an interactive team selection. Invalid or missing IDs leave the form unassigned and do not block task creation. Edit routes ignore these parameters so existing assignments always come from the stored task.

## Edit Forms

The existing forms remain responsible for creation and editing only:

- `AgentPage` treats only `/agents/:agentId/edit` as edit mode.
- `TeamPage` treats only `/teams/:teamId/edit` as edit mode.
- After an edit save, navigate to that entity's detail page.
- Cancel and back controls on an edit return to that entity's detail page.
- After a create save, navigate to the newly created detail page when the API returns an ID; otherwise fall back to the list.
- Delete is removed from edit forms so the destructive action has one canonical home on the detail page.

## Loading and Error States

Both detail pages use the existing full-page loading and retryable error components. Missing entities render the same retryable not-found state rather than a partially populated header. Their task sections expose loading and retryable error states instead of coercing unavailable data to an empty list. The assignment picker keeps a persistent disclosure trigger, moves focus into the labelled checklist, disables every control while saving, and restores focus when it closes. Task status is exposed as an accessible description. Buttons preserve the established responsive 44-pixel mobile touch targets.

## Testing

Frontend tests will verify:

- Route declarations map detail and edit URLs to different components and contain no team interaction routes.
- Agent and team list rows still target the canonical detail URLs.
- Agent detail renders configuration and all three header actions.
- Agent Interact writes the existing Home selection/sentinel keys and navigates to `/home`.
- Agent and team Delete actions call the correct API and return to their lists after confirmation.
- Agent detail lists assigned tasks and can append the agent to selected existing tasks without changing their other assignment fields.
- Team detail renders members, leader, tasks, and edit action with canonical detail links.
- Team detail can assign selected existing tasks to the team and synchronizes their assigned agents to current team members.
- Existing-task assignment uses the narrow PATCH endpoint, preserves non-assignment task fields, reconciles partial failures, reports progress/errors accessibly, and refreshes the assigned-task list.
- Agent and team `New task` links pass the correct query parameter.
- Task create mode consumes valid assignment parameters, ignores invalid parameters, and edit mode ignores all prefill parameters.
- Edit forms load from `/edit`, save back to detail, and use detail as their cancel/back destination.
- Existing accessibility, lint, type-check, build, and browser workflow checks remain green.

## Non-goals

- No redesign of the task persistence model or execution API.
- No new team chat or orchestration behavior.
- No redesign of task details.
- No compatibility redirects for removed `/interact` URLs because those routes are not part of the new navigation contract.
