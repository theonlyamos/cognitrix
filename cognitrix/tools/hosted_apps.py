"""App tools: agents publish a live web app (a server with its own data) that
people open from a stable link, and check how it's doing.

Added per turn to chats and task runs that belong to a signed-in person, next
to the artifact tools (see ``Session._run_turn`` and
``tasks.orchestrator._add_automation_tools``).
"""
from __future__ import annotations

from pathlib import Path

from cognitrix.common import folders as folder_paths
from cognitrix.common.safe_exec import PathEscapesRoot
from cognitrix.hosted_apps import store
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import EntityRef, ToolOutcome, current_execution_context

APP_TOOL_CATEGORY = 'apps'


def _owner() -> str | None:
    ctx = current_execution_context()
    if ctx.restricted or not ctx.user_id:
        return None
    return str(ctx.user_id)


def _agent_id(parent) -> str:
    ctx = current_execution_context()
    return str(ctx.agent_id or getattr(parent, 'id', '') or '')


@tool(category=APP_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'])
async def publish_app(title: str, start_command: str, folder: str = '', install_command: str = '', note: str = '',
                      app_id: str = '', description: str = '', use_cognitrix_api: bool = False, branch: str = '',
                      *, parent=None) -> ToolOutcome:
    """Publish a live web app people open from a link that never changes: an internal tool, dashboard,
    form, review queue, or a staging copy of a project. Unlike Publish Artifact (one static page), an app
    is a real server with its own backend and database. It runs in the agents' sandboxed computer
    (or on this machine when that's off), sleeps when nobody uses it and wakes on the next visit.

    Build it first: write the code into its own folder in the apps folder, ~/.cognitrix/apps/<name>
    (every agent can read and write there without asking; see the build-app skill), and try it with the
    shell if you can. Then publish that folder. A folder elsewhere is copied into the apps folder.
    The running app is a copy, so later edits need another publish.
    Rules the app must follow:
    - Serve HTTP on 127.0.0.1 at the port in the PORT environment variable.
    - Use RELATIVE links and fetch paths ("api/items", "./static/app.js", Vite base "./"), never
      ones starting with "/": the app is served under a path prefix.
    - Keep data (SQLite files, uploads) in the folder in APP_DATA_DIR; it survives new versions.
      Everything else is replaced on every publish.
    - It runs in a sandboxed browser frame: no cookies or localStorage. The viewer is signed in to
      Cognitrix; read who they are from the X-Cognitrix-User-Name / -Email / -Id request headers
      (percent-encoded UTF-8: decode with unquote).
    - Python 3 and Node.js are available. Dependency folders (node_modules, .venv) are not copied;
      install them with install_command (e.g. "pip install -r requirements.txt" or "npm ci").
    - No websockets yet: poll instead.

    If the new version doesn't answer HTTP within 90 seconds it is marked failed and the result shows
    its log; the live version keeps serving. Fix it and publish again with the same app_id.

    Args:
        title (str): Short name, 2-5 words. Also picks the link (/apps/open/<name>).
        folder (str): The app's folder, e.g. ~/.cognitrix/apps/team-dashboard. A name alone means that
            folder inside the apps folder; empty means the apps folder subfolder named after the title.
        start_command (str): Shell command run in the app folder to start it, e.g. "python3 app.py" or "node server.js".
        install_command (str): Optional command run once per version before starting, e.g. "npm ci".
        note (str): One line on what changed in this version.
        app_id (str): Id or name of an app to update; empty for a new one.
        description (str): One sentence on what the app is for.
        use_cognitrix_api (bool): Give the app a Cognitrix API key (COGNITRIX_API_URL, COGNITRIX_API_KEY env)
            so it can run agents and read tasks. Only when the app needs it.
        branch (str): For a preview of a project: the git branch it was built from.
    """
    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Apps need a signed-in person to belong to.')
    ctx = current_execution_context()
    folder = str(folder or '').strip()
    if folder and (Path(folder).expanduser().is_absolute() or folder.startswith('~')):
        # Only folders this agent may read can be published.
        try:
            folder = str(folder_paths.resolve_path(folder))
        except PathEscapesRoot as exc:
            return ToolOutcome.failure('folder_not_allowed', str(exc))
    try:
        app, row, created = await store.publish(
            user_key=user_key, title=title, folder=folder, command=start_command, install=install_command,
            note=note, app_id=str(app_id or '').strip(), description=description,
            use_cognitrix_api=bool(use_cognitrix_api), branch=branch, agent_id=_agent_id(parent),
            session_id=str(ctx.session_id or ''), task_id=str(ctx.task_id or ''),
        )
    except store.AppError as exc:
        return ToolOutcome.failure(exc.code, exc.message)
    entity = [EntityRef(type='hosted_app', id=str(app.id), name=app.title)]
    if row.status != 'live':
        live = f' Version {app.version} keeps serving.' if app.version else ''
        log = f'\nLog:\n{row.log[-3000:]}' if row.log else ''
        return ToolOutcome.failure(
            'did_not_start',
            f'Version {row.number} of "{app.title}" (app_id {app.id}) did not start: {row.error}{live}{log}',
        )
    what = 'Published' if created else f'Updated to version {row.number}:'
    where = "the agents' sandboxed computer" if app.runtime == 'sandbox' else 'this machine'
    return ToolOutcome.success(
        f'{what} "{app.title}" (app_id {app.id}, version {row.number}), running on {where}. '
        f'It is private to the person until they share it; the link is /apps/open/{app.slug}. '
        'The person sees a card in the chat that opens it.',
        entities=entity,
    )


@tool(category=APP_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'])
async def app_status(app_id: str = '', log_lines: int = 80, *, parent=None) -> ToolOutcome:
    """See an app's status, versions and the end of its log, to debug it. With no app_id, lists the
    apps you can update.

    Args:
        app_id (str): Id or name of the app; empty to list apps.
        log_lines (int): How many log lines to show (up to 400).
    """
    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Apps need a signed-in person.')
    if not str(app_id or '').strip():
        rows = [(a, r) for a, r in await store.visible(user_key) if store.can_edit(r)]
        if not rows:
            return ToolOutcome.success('No apps yet.')
        return ToolOutcome.success('\n'.join(
            f'- {a.title} (app_id {a.id}, name {a.slug}): {a.status}, version {a.version or "-"}'
            for a, _ in rows[:50]))
    try:
        app, role = await store.app_for(app_id, user_key)
    except store.AppError as exc:
        return ToolOutcome.failure(exc.code, exc.message)
    if not store.can_edit(role):
        # Commands, folders and logs are for the people who can change the app.
        return ToolOutcome.failure('not_allowed', f'You can use "{app.title}" but not see its versions or logs. '
                                   'Ask its owner for edit access.')
    lines = [f'"{app.title}" (app_id {app.id}): {app.status}, live version {app.version or "none"}, '
             f'runs on {app.runtime}, start command: {app.command}'
             + (f', install: {app.install}' if app.install else '') + f', folder: {app.folder}']
    if app.last_error:
        lines.append(f'Last error: {app.last_error}')
    for row in (await store.versions(app))[:8]:
        lines.append(f'- v{row.number} {row.status}{": " + row.error if row.error else ""}'
                     f'{" (" + row.note + ")" if row.note else ""}')
    log = await store.logs(app, max(1, min(int(log_lines or 80), 400)))
    lines.append('Log:\n' + (log or '(empty)'))
    return ToolOutcome.success('\n'.join(lines))
