# Projects

A project groups chats and tasks that share one context. Projects are managed
on the Projects page (`/projects`); they are not listed in the sidebar.

## What a project holds

| Field | What it does |
| --- | --- |
| `instructions` | Added to the system prompt of every chat and task in the project. A new project made from a folder reads `AGENTS.md`, `CLAUDE.md` or `.cursorrules` when none are typed. |
| `agents` | Who works in it. The first one starts new chats. |
| `folders` | Working folders new chats start with, instead of the agent's defaults. |
| `knowledge` | Collections every agent in the project can search, on top of its own. |
| `computer` | `sandbox` (one shared sandbox), `''` (each agent's own) or a paired PC id. |
| `pc_folders` | On a paired PC: the PC folders the project uses. Empty means all of them. |
| `use_account_memory` | Whether agents also see the person's account-wide memories. |

## How a turn uses it

`Session.__call__` loads the project (from `Session.project_id`, or the task's
`Task.project_id`) and sets `cognitrix.projects.context.current_project` for
the turn. Code inside the turn reads it from there:

- the prompt gets a `## Project: <name>` note with the instructions;
- Search Knowledge sees the project's collections;
- memories with `scope='project'` are seen only inside their project, and the
  Memory tool saves there by default inside a project;
- with the shared sandbox, `sandbox_identity()` returns `project-<id>`, so
  every agent of the project uses one desktop, Linux user and `~/work` folder;
- on a paired PC, the prompt lists only the project's folders and every call
  carries `only_folders`, which the PC connector enforces.

API calls about a chat outside a turn (the changes view) use
`context.scope_for(session)` so they resolve the same sandbox.

## File changes

Each chat's changes view (`/sessions/{id}/changes`) reads the project's
shared sandbox copy. `GET /projects/{id}/changes` gathers every chat's changes
for the project page's Changes tab; a shared sandbox copy appears once, under
the newest chat. Accept, reject, undo and copy back use the chat's own API.

## Always allow

The approval card offers "Always allow":

- for an ordinary tool, it sets the tool to `allow` in the agent's
  `tool_permissions` (only on the person's own agent; on someone else's agent
  it holds for the chat);
- for a full-shell command or a command on a paired PC, it saves a
  `CommandRule` (person, computer, prefix such as `git status`). Later commands
  run without asking only when they start with that prefix and are a single
  plain command (no `;`, `&&`, `|`, redirects, `$(...)` or backticks). Some
  programs (`rm`, `curl`, `sudo`, `bash -c`, bare interpreters) are never
  offered. Rules are listed and removed with `GET/DELETE /agents/approval/rules`.

## Not built yet

Sharing projects with other people, project templates, a lead agent and
overview board, and a project files folder.
