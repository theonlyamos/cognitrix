# Agent and Team Detail Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add canonical agent/team detail pages with header actions, task assignment for existing and new tasks, and dedicated `/edit` routes.

**Architecture:** Route list clicks to read-only detail components and retain the current forms only for `/new` and `/:id/edit`. A shared `TaskAssignmentPanel` owns existing-task assignment through a narrow partial-update API, while query parameters prefill the existing task creation form. Agent chat handoff reuses Home's persisted agent/session keys.

**Tech Stack:** React 18, TypeScript, React Router 6, Tailwind CSS, Vitest, Testing Library, Axios API wrapper.

## Global Constraints

- Preserve the existing task, agent, and team contracts while adding only `PATCH /tasks/:taskId/assignment` for ownership-only updates.
- Preserve all unrelated uncommitted remediation changes.
- Use existing responsive button, loading, error, checklist, and header patterns.
- Detail-page destructive actions require confirmation and redirect only after success.
- Team assignment sets `team_id` and synchronizes `assigned_agents` to current team members.
- Agent assignment appends the agent while preserving all other task fields and assignments.
- Removed `/interact` routes receive no compatibility redirects.

---

### Task 1: Route and Detail-Page Contracts

**Files:**
- Create: `frontend/src/pages/AgentDetail.tsx`
- Create: `frontend/src/pages/TeamDetail.tsx`
- Delete: `frontend/src/pages/TeamInteraction.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/AgentPage.tsx`
- Modify: `frontend/src/pages/TeamPage.tsx`
- Test: `frontend/src/pages/entity-details.test.tsx`

**Interfaces:**
- Consumes: `useResource<T>(path)`, `api.delete(path)`, React Router params/navigation, existing `Button`, `LoadingState`, `ErrorState`, and `Chevron` components.
- Produces: default `AgentDetail` and `TeamDetail` route components at canonical `/:id` paths; edit forms at `/:id/edit`.

- [ ] **Step 1: Write failing route/detail tests**

Create tests that read `src/App.tsx` and assert these exact route mappings, then render mocked detail data:

```tsx
expect(appSource).toContain('<Route path="/agents/:agentId" element={<AgentDetail />} />');
expect(appSource).toContain('<Route path="/agents/:agentId/edit" element={<AgentPage />} />');
expect(appSource).toContain('<Route path="/teams/:teamId" element={<TeamDetail />} />');
expect(appSource).toContain('<Route path="/teams/:teamId/edit" element={<TeamPage />} />');
expect(appSource).not.toContain('/interact');

expect(screen.getByRole('heading', { name: 'Agent One' })).toBeInTheDocument();
expect(screen.getByRole('link', { name: 'Interact' })).toHaveAttribute('href', '/home');
expect(screen.getByRole('link', { name: 'Edit' })).toHaveAttribute('href', '/agents/agent-1/edit');
expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pnpm exec vitest run src/pages/entity-details.test.tsx`

Expected: FAIL because `AgentDetail`, `TeamDetail`, and the new route declarations do not exist.

- [ ] **Step 3: Implement minimal route split and detail pages**

In `App.tsx`, lazy-load both detail components and map canonical and edit routes separately. Move the current team overview into `TeamDetail`, add delete/error behavior, replace its edit links with `/:id/edit`, and remove all interaction routes. Build `AgentDetail` with read-only configuration, system prompt, tools, header actions, and delete/error behavior.

For Interact, write the existing Home handoff keys before navigation:

```tsx
const prepareChat = () => {
  localStorage.setItem('selectedAgentId', agent.id);
  localStorage.setItem(`chatSession:${agent.id}`, '');
};

<Button asChild size="sm">
  <Link to="/home" onClick={prepareChat}>Interact</Link>
</Button>
```

Update edit forms so edit saves and cancel/back links return to details; creation uses the returned entity ID when available. Remove form-level delete and team interaction actions.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pnpm exec vitest run src/pages/entity-details.test.tsx`

Expected: PASS.

### Task 2: Shared Existing-Task Assignment

**Files:**
- Create: `frontend/src/components/TaskAssignmentPanel.tsx`
- Modify: `cognitrix/api/routes/tasks.py`
- Modify: `frontend/src/pages/AgentDetail.tsx`
- Modify: `frontend/src/pages/TeamDetail.tsx`
- Test: `tests/test_scheduler.py`
- Test: `frontend/src/pages/entity-details.test.tsx`

**Interfaces:**
- Consumes: task summaries from `useResource<TaskRecord[]>('/tasks')`, entity ID/type, current team member IDs, and the tasks resource's `refetch` function.
- Produces: `TaskAssignmentPanel({ mode, entityId, memberIds, tasks, onAssigned })` and linked assigned-task sections on both detail pages.

- [ ] **Step 1: Add failing assignment tests**

Cover opening the picker, choosing a task, and applying each assignment:

```tsx
await user.click(screen.getByRole('button', { name: 'Assign task' }));
await user.click(screen.getByRole('checkbox', { name: /Task Two/ }));
await user.click(screen.getByRole('button', { name: 'Assign selected' }));

expect(api.patch).toHaveBeenCalledWith('/tasks/task-2/assignment', {
  assigned_agents: ['agent-existing', 'agent-1'],
  team_id: 'team-existing',
});
```

For teams, expect `team_id: 'team-1'` and `assigned_agents` equal to current member IDs. Also cover partial-error reconciliation, disabled progress state, focus restoration, labelled checklist semantics, and assigned-task links.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pnpm exec vitest run src/pages/entity-details.test.tsx`

Expected: FAIL because no assignment panel or narrow assignment endpoint exists.

- [ ] **Step 3: Implement minimal shared panel**

Define the minimal task summary consumed by the picker:

```ts
export interface TaskRecord {
  id: string;
  title: string;
  status?: string;
  assigned_agents?: string[];
  team_id?: string | null;
  [key: string]: unknown;
}
```

Add `PATCH /tasks/:taskId/assignment` and implement it with `Task.update_one` over only `assigned_agents` and `team_id`. Filter eligible tasks by mode, render the existing `CheckList`, and settle selected PATCH requests independently. Agent mode de-duplicates appended IDs; team mode replaces the assignment fields with destination team/member values. Refresh any successes, preserve only failed selections, close/reset on complete success, and expose accessible progress and failure messages.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pnpm exec vitest run src/pages/entity-details.test.tsx`

Expected: PASS.

### Task 3: Preassigned New Tasks

**Files:**
- Modify: `frontend/src/pages/TaskPage.tsx`
- Modify: `frontend/src/pages/AgentDetail.tsx`
- Modify: `frontend/src/pages/TeamDetail.tsx`
- Test: `frontend/src/pages/task-prefill.test.tsx`

**Interfaces:**
- Consumes: create-only query parameters `agentId` and `teamId`, plus loaded agent/team lists.
- Produces: initial task form selection matching the requested valid entity; edit routes ignore query parameters.

- [ ] **Step 1: Write failing prefill tests**

Render `/tasks/new?agentId=agent-1` and `/tasks/new?teamId=team-1` and assert checked assignments/team selection. Render invalid IDs and `/tasks/task-1/edit?agentId=agent-2` to assert no query-driven mutation.

Also assert detail links:

```tsx
expect(screen.getByRole('link', { name: 'New task' }))
  .toHaveAttribute('href', '/tasks/new?agentId=agent-1');
```

- [ ] **Step 2: Run focused test and verify RED**

Run: `pnpm exec vitest run src/pages/task-prefill.test.tsx`

Expected: FAIL because `TaskPage` ignores query parameters.

- [ ] **Step 3: Implement create-only query prefill**

Use `useSearchParams` and one guarded effect that runs only when `!editing`. A valid `teamId` wins when both parameters are present and uses the same member-prefill semantics as `changeTeam`; otherwise a valid `agentId` is added. Do not overwrite manual choices after initialization.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pnpm exec vitest run src/pages/task-prefill.test.tsx src/pages/entity-details.test.tsx`

Expected: PASS.

### Task 4: Regression and Browser Verification

**Files:**
- Modify tests only if a real regression is uncovered.

**Interfaces:**
- Consumes: completed route/detail/assignment behavior.
- Produces: fresh automated and browser evidence that the approved design works end to end.

- [ ] **Step 1: Run frontend quality gates**

Run:

```powershell
pnpm test
pnpm lint
pnpm build
```

Expected: all tests and build pass; lint has zero errors.

- [ ] **Step 2: Run repository checks**

Run: `git diff --check` and inspect `git status --short` plus the focused diff. Expected: only intended feature files plus preserved pre-existing remediation changes.

- [ ] **Step 3: Verify in the in-app browser**

Confirm:

- Agent/team list rows open canonical detail pages.
- Agent Interact opens a blank Home conversation with that agent selected.
- Edit opens `/edit`, save/cancel return to details.
- Existing-task assignment updates each detail's task list.
- New-task links prefill agent/team assignment.
- Delete confirmation works on temporary test entities only.
- Fresh console contains no warnings/errors caused by the feature.

- [ ] **Step 4: Clean browser-created artifacts and report**

Delete only the temporary tasks/entities created for this verification, leave the app on a canonical detail or list page, and summarize exact test/build/browser evidence.
