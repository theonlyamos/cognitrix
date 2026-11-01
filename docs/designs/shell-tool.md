# Shell tool (Bash)

How the Bash tool runs commands, and why. Modeled on the shell tools in Claude
Code (Bash + BashOutput + KillShell) and Codex (exec with a sandbox and
approvals, "known safe" read-only commands that skip approval).

## Two ways to run

| Kind | When | How it runs | Approval |
| --- | --- | --- | --- |
| Allowlisted | Every part is an allowlisted program joined by `\|`, `&&`, `\|\|`, `;`, plus the `cd` and `pwd` builtins | Cognitrix runs each program itself with `shell=False` and wires the pipes; no shell parses the text | Read-only commands (ls, cat, grep, git status/log/diff, ...) run at once. Others (python, npm, git commit, mkdir, ...) ask |
| Full shell | Anything else: redirection, `$VARS`, globs, curl, rm, docker, PowerShell cmdlets, scripts | bash/zsh/sh on Linux and macOS, PowerShell on Windows (`COGNITRIX_SHELL` overrides, e.g. Git Bash) | Asks every time. The tool only runs it when a person approved that call |

`cognitrix.common.shell.plan_command` decides the kind; `shell_risk` turns it
into the approval gate's low/medium/high rating. The agent loop passes a
runtime-only `approved` flag to Bash, true only when a person approved the call
(not auto-approval, bypass mode or a tool set to never ask), so a full-shell
command can't run without a person seeing it.

Settings:

- `COGNITRIX_SHELL_MODE=ask` (default) or `allowlist` (full shell refused).
- `COGNITRIX_SANDBOX_SHELL=1` / `--dangerously-skip-permissions`: full shell
  with no approval, for throwaway containers only (unchanged).
- Shared web servers still deny all host processes; only the desktop app and
  the CLI can run commands (unchanged).

## Behaviour

- **Working directory persists** per chat session. `cd sub` in one call means
  the next call starts in `sub`. It never leaves the working folder: an
  allowlisted `cd` outside is refused, and a full-shell command that ends
  outside is pulled back to the working folder with a note.
  `working_dir` runs one command elsewhere without changing it.
- **Timeouts** default to 120 s, max 600 s. A timeout kills the whole process
  tree (process group on POSIX, `taskkill /T` on Windows) and returns the
  output printed so far.
- **stdin is closed**, so nothing hangs on a prompt. `GIT_PAGER=cat`,
  `GIT_TERMINAL_PROMPT=0`.
- **Background commands**: `run_in_background=true` returns a shell id.
  *Bash Output* returns new output since the last read (optional regex
  filter) and the status; *Kill Shell* stops it and its children. Per-user,
  at most 8 running, 2 MB buffer each, all stopped when Cognitrix exits.
  Agents with Bash get the two helpers automatically for each turn.
- **Output** over 30,000 characters keeps the first third and the last two
  thirds, with a note on what was cut. Failed commands end with the exit code.
- **Windows**: UTF-8 output (PowerShell's output encoding is set, and
  `decode_output` falls back to the code page). Allowlisted programs are found
  with `shutil.which`, so `npm`/`pnpm` (`.cmd` files) work; `dir`, `type` and
  `echo` go through `cmd /c`. `%`, `^` and `!` need the full shell there.
  No console windows flash up from the desktop app.

## Not done

- Environment variables don't persist between calls (same as Claude Code).
- No OS-level sandbox (Codex uses Seatbelt/Landlock). Approval is the boundary
  for the full shell.
- No interactive stdin (Codex's `write_stdin`).
