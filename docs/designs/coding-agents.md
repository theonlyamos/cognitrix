# Coding Agent tool

Lets a Cognitrix agent hand a coding job to a coding agent CLI installed on the
same computer, the way a lead hands a ticket to a developer. Builds on the
Bash tool's background jobs (see [shell-tool.md](shell-tool.md)).

## What the agent sees

`Coding Agent(task, agent='auto', mode='edit', working_dir=None, model=None, wait_seconds=0)`

- `agent`: `claude` (Claude Code), `codex`, `gemini` (Gemini CLI), or `auto`
  (the first installed, in that order). `list` shows what's installed and
  never asks.
- `mode`: `read_only`, `edit` (default) or `full`; see below.
- `working_dir`: inside the working folder; defaults to the chat's current
  Bash directory.
- `wait_seconds`: wait up to 600 s and return the result directly; 0 returns
  at once with a shell id.

The job runs in the same background registry as `Bash(run_in_background=true)`,
so **Bash Output** reads its progress and **Kill Shell** stops it. Agents with
the Coding Agent tool get both helpers for each turn. When the job ends, the
next read appends how long it took and `git status` of the folder, so the
calling agent can see what changed without another tool call.

## How each CLI is run

The task text goes in on **stdin**, never on the command line. npm installs
these CLIs as `.cmd` files on Windows, and cmd.exe would otherwise parse `&`,
`|` and `%` in the task. The model name is checked against a strict pattern.

| Mode | Claude Code (`claude -p --output-format text`) | Codex (`codex exec --skip-git-repo-check -`) | Gemini CLI (`gemini`) |
| --- | --- | --- | --- |
| read_only | `--permission-mode plan` | `--sandbox read-only` | `--approval-mode default` |
| edit | `--permission-mode acceptEdits` (its shell commands are refused) | `--sandbox workspace-write` (runs commands in the folder sandbox) | `--approval-mode auto_edit` |
| full | `--permission-mode bypassPermissions` | `--sandbox danger-full-access` | `--approval-mode yolo` |

The CLIs use their own sign-in (`claude` once, `codex login`, `gemini` once) or
the API key variables already in the environment. Cognitrix doesn't pass its
own provider keys to them.

## Safety

- **Every run asks.** The risk card says which CLI, which mode, which folder
  and the start of the task. `edit` and `read_only` are MEDIUM, `full` is
  HIGH. Like the full shell, the tool only runs when a person approved that
  exact call: remembered approvals, auto-approval, bypass mode and
  `approval_mode='never'` don't unlock it (`COGNITRIX_SANDBOX_SHELL=1` does,
  for throwaway containers).
- **Local only.** Same host-process rule as Bash: off on shared web servers,
  on in the desktop app and the CLI.
- **Inside the working folder.** `working_dir` can't leave it.
- **Time limit.** A job is stopped (whole process tree) after
  `COGNITRIX_CODING_AGENT_TIMEOUT` seconds, default 3600.
- At most 8 background jobs per user, shared with Bash.

## Settings

- `COGNITRIX_CODING_AGENT_TIMEOUT`: seconds before a job is stopped (min 60).
- `COGNITRIX_CLAUDE_PATH`, `COGNITRIX_CODEX_PATH`, `COGNITRIX_GEMINI_PATH`: use
  a CLI that isn't on PATH.
- `GET /system/info` lists installed CLIs as `coding_agents`; the agent editor
  shows them under the tool list.

## Not done

- No follow-up turns in the same coding-agent session (`claude --resume`,
  `codex exec resume`). Each call is a fresh job.
- No streaming of the coding agent's intermediate steps into the chat; the
  calling agent reads them with Bash Output.
- No per-folder approval or working-folder list yet; that comes with the
  working folders design.
- Other CLIs (Aider, OpenCode, Cursor CLI) can be added to `CLIS` in
  `cognitrix/common/coding_agents.py`.
