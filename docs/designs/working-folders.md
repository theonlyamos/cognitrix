# Working folders

Agents can work in several folders per chat. Each folder is **read only** or
**read & write**, and one is the **primary** folder: relative paths and the
shell start there.

## Where folders come from

- `Agent.folders` holds an agent's default folders. `None` (never set) means
  the app's working folder (`COGNITRIX_TOOLS_ROOT`, read & write), which is how
  every agent worked before. An empty list means each chat gets a private
  scratch folder under `~/.cognitrix/workspaces/`.
- `Session.folders` is the chat's own list. The first web/task turn copies the
  agent's defaults; after that the chat keeps its copy. The composer's folder
  chips change it (`PUT /api/v1/folders/sessions/{id}`), and a new chat can
  send `folders` with its first message.
- CLI turns keep the single app working folder.
- Sub-agents and delegated calls get the parent's folders, read only.

## How tools use them

`cognitrix/common/folders.py` is the one place paths are resolved. The turn's
list travels in `ToolExecutionContext.folders`.

- Relative paths join the primary folder; absolute paths must fall inside a
  listed folder after symlinks are resolved.
- Write/Edit need a read & write folder (`FolderReadOnly`).
- Protected places (`~/.ssh`, cloud CLI config, browser profiles, the
  Cognitrix data folder, system folders) are refused even inside a listed folder.
- Bash starts in the primary folder; `working_dir` may be any listed folder.
  The shell can't enforce read only, so a command in a read only folder that
  needs approval says so on the approval card.
- The system prompt lists the folders with their access.

## Asking for more

`Request Folder` (`path`, `access`, `reason`) is added to web/task turns when
`Agent.can_request_folders` is on. It always needs a person's approval, even
with the browser's auto-approve on; once approved the folder is added to the
chat for the rest of the turn and later turns.

## Which folders can be added

`COGNITRIX_ALLOWED_FOLDERS` (os.pathsep separated) limits where folders can
come from. Without it the desktop app allows anywhere that isn't protected, and
a web server allows only folders inside its working folder.

## AGENTS.md

Every chat, task run and CLI turn follows the AGENTS.md files of its working
folders (`cognitrix/common/agents_md.py`), read the way Codex reads them:

- In each folder the first of `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`,
  `.cursorrules` counts.
- For a folder inside a git repository, the files from the repository root
  down to the folder all go into the system message, root first, so the
  closest one comes last and wins. Parent folders are only read where the
  install allows folders (never above the home folder or in a protected place).
- Subfolders with their own file are listed. The first time a chat reads or
  changes a file under one, that file's instructions come back with the Read,
  Write or Edit result.
- At most 32 KiB goes into the prompt.
- Run in sandbox uses the host folders' files (they are copied in). A paired
  PC reads the AGENTS.md of its first shared folder on the PC itself; deeper
  ones come back with Read results there too.
- A project's instructions that were read from the same file aren't repeated.

The folder picker shows "Follows AGENTS.md" under each folder
(`GET /folders/instructions?path=`).

## Not built yet

- Server-side folder browser and admin "allowed folders" screen (web mode).
- "Allow and add to agent defaults" on the approval card.
- Per-task folder lists (tasks use the agent's defaults; scratch is per task).
- A real OS sandbox for the shell.
