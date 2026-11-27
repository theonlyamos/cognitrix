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


def _has_saved_tests(app) -> bool:
    path = store.tests_file(app)
    return bool(path is not None and path.is_file())


@tool(category=APP_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'])
async def publish_app(title: str, start_command: str, folder: str = '', install_command: str = '', note: str = '',
                      app_id: str = '', description: str = '', use_cognitrix_api: bool = False, branch: str = '',
                      icon: str = '', connected_apps: list | None = None, credentials: list | None = None,
                      secrets: list | None = None, *, parent=None) -> ToolOutcome:
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
    - Give it an icon: a square icon.svg (or a 512 x 512 icon.png) in its folder. It shows on the Apps
      page and becomes the shortcut icon when someone installs the app on their computer.

    The app's SERVER code can use the person's connected apps (Gmail, Calendar, any MCP server), their
    saved sign-ins and its own secrets, with nobody looking (a nightly sync, a webhook). You can only ask:
    name what it needs in connected_apps / credentials / secrets, and the person allows it on the app's
    page under Access. Until they do, those calls answer 403 with a message saying so: show that in the
    app instead of crashing. How the code calls them is in the build-app skill ("Using connected apps
    and saved sign-ins from the server"). Never write a key or password into the app's files.

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
        icon (str): The icon file inside the folder, e.g. "static/logo.svg"; empty looks for icon.svg,
            icon.png (also in static/, public/, assets/) and favicon.svg/png.
        connected_apps (list): Names of the person's connected apps its server code needs, e.g. ["Gmail"].
            The whole list each time; [] takes the ask back. Leave out to keep what was asked before.
        credentials (list): Names of saved sign-ins (see List Credentials) its server code needs to send
            requests with, e.g. ["GITHUB_TOKEN"]. The app never sees the values.
        secrets (list): Names of environment variables the person must fill in for this app only,
            e.g. ["STRIPE_WEBHOOK_SECRET"]. They type the values on the app's page; you never see them.
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
            session_id=str(ctx.session_id or ''), task_id=str(ctx.task_id or ''), icon=str(icon or '').strip(),
            wants={'apps': connected_apps, 'credentials': credentials, 'secrets': secrets},
        )
    except store.AppError as exc:
        return ToolOutcome.failure(exc.code, exc.message)
    icon_note = store.icon_note(app)
    entity = [EntityRef(type='hosted_app', id=str(app.id), name=app.title)]
    access_note = await _access_note(app)
    if row.status != 'live':
        live = f' Version {app.version} keeps serving.' if app.version else ''
        log = f'\nLog:\n{row.log[-3000:]}' if row.log else ''
        return ToolOutcome.failure(
            'did_not_start',
            f'Version {row.number} of "{app.title}" (app_id {app.id}) did not start: {row.error}{live}{log}'
            + (f'\n{icon_note}' if icon_note else '') + (f'\n{access_note}' if access_note else ''),
        )
    what = 'Published' if created else f'Updated to version {row.number}:'
    where = "the agents' sandboxed computer" if app.runtime == 'sandbox' else 'this machine'
    return ToolOutcome.success(
        f'{what} "{app.title}" (app_id {app.id}, version {row.number}), running on {where}. '
        f'It is private to the person until they share it; the link is /apps/open/{app.slug}. '
        'The person sees a card in the chat that opens it. Now look at it with Check App and click through it '
        'with Test App' + (' (no steps re-runs its saved tests)' if _has_saved_tests(app) else '')
        + ' before you say it is done.' + (f' {icon_note}' if icon_note else '')
        + (f' {access_note}' if access_note else ''),
        entities=entity,
    )


async def _access_note(app) -> str:
    """What the app is still waiting for from its owner, for the agent to pass on."""
    from cognitrix.hosted_apps import access

    row = await access.row_for(app)
    if row is None:
        return ''
    waiting = await access.waiting(app, row)
    parts = []
    if waiting['apps']:
        parts.append('connected apps: ' + ', '.join(waiting['apps']))
    if waiting['credentials']:
        parts.append('saved sign-ins: ' + ', '.join(waiting['credentials']))
    if waiting['secrets']:
        parts.append('secrets to fill in: ' + ', '.join(waiting['secrets']))
    notes = []
    if parts:
        notes.append(f'Its server code is waiting for the person to allow these on the app\'s page '
                     f'(/apps/{app.slug}, Access tab): ' + '; '.join(parts) + '. Tell them, in plain words. '
                     'Filling in a secret needs a restart from that tab.')
    if row.paused:
        notes.append('Access to connected apps, saved sign-ins and secrets is paused until the owner checks '
                     'this version on the Access tab.')
    return ' '.join(notes)


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


@tool(category=APP_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'])
async def check_app(app_id: str, path: str = '', phone: bool = False, *, parent=None) -> ToolOutcome:
    """Look at a published app the way a person would: it is woken up and opened in a headless
    browser where it runs, and you get a screenshot plus any script errors, console errors and
    failed requests. Use it after every Publish App, fix what it shows, publish again, and only
    then tell the user the app is ready. Never asks for approval.

    Args:
        app_id (str): Id or name of the app.
        path (str): A page inside the app, e.g. "settings" or "items?id=3"; empty for its home page.
        phone (bool): Look at it on a phone-sized screen (390 wide) instead of a desktop (1280 wide).
    """
    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Apps need a signed-in person.')
    try:
        app, role = await store.app_for(app_id, user_key)
        out = await store.check(app, user_key, path=path, phone=bool(phone))
    except store.AppError as exc:
        return ToolOutcome.failure(exc.code, exc.message)
    if not out.get('ok'):
        # Logs are for the people who can change the app (as in App Status).
        log = await store.logs(app, 40) if store.can_edit(role) else ''
        return ToolOutcome.failure('did_not_load', f'"{app.title}" did not load: {out.get("error") or "no answer"}'
                                   + (f'\nLog:\n{log[-2000:]}' if log else ''))
    page = f'/{str(path or "").lstrip("/")}'
    size = 'phone (390x844)' if phone else 'desktop (1280x800)'
    lines = [f'Checked "{app.title}" {page} on a {size} screen: HTTP {out.get("status") or "?"}'
             + (f', title "{out["title"]}"' if out.get('title') else '') + '.']
    if out.get('note'):
        lines.append(out['note'])
    if not out.get('text_length'):
        lines.append('The page shows no text: it may be blank or still loading (check the screenshot).')
    for label, key in (('Script errors', 'errors'), ('Console errors', 'console'),
                       ('Failed requests (paths are relative to the app)', 'failed')):
        items = out.get(key) or []
        if items:
            lines.append(f'{label}:\n' + '\n'.join(f'- {item}' for item in items))
    if not any(out.get(k) for k in ('errors', 'console', 'failed')):
        lines.append('No script errors, console errors or failed requests.')
    shot = out.get('screenshot') or ''
    if shot:
        from cognitrix.tools.misc import queue_image

        lines.append('Screenshot attached: look at it for layout problems before you call the app done.'
                     if queue_image('data:image/png;base64,' + shot)
                     else '(This chat cannot show you the screenshot.)')
    return ToolOutcome.success('\n'.join(lines), entities=[EntityRef(type='hosted_app', id=str(app.id), name=app.title)])


def _test_report(title: str, label: str, out: dict) -> tuple[bool, list[str], list[str]]:
    """(passed, report lines, screenshots as data URIs) for one test run."""
    steps = out.get('steps') or []
    total = int(out.get('total') or len(steps))
    done = sum(1 for s in steps if s.get('ok'))
    passed = bool(out.get('passed'))
    verdict = 'PASSED' if passed else 'FAILED'
    lines = [f'{label}: {verdict}, {done} of {total} steps passed.']
    for row in steps:
        mark = '✓' if row.get('ok') else '✗'
        text = f'{row.get("n")}. {mark} {row.get("do")}'
        if row.get('note'):
            text += f' ({row["note"]})'
        if not row.get('ok'):
            text += f': {row.get("error")}'
        lines.append(text)
        for key, name in (('errors', 'script error'), ('console', 'console error'), ('failed', 'failed request')):
            for item in row.get(key) or []:
                lines.append(f'   {name}: {item}')
        for item in row.get('dialogs') or []:
            lines.append(f'   dialog: "{item}"')
    if not passed and done == total and out.get('errors'):
        lines.append('Every step passed, but the page threw script errors (listed above), so the test fails.')
    if total > len(steps) and steps and not steps[-1].get('ok'):
        lines.append(f'The {total - len(steps)} steps after it were not run.')
    if out.get('address'):
        lines.append(f'Address when it stopped: {out["address"]}')
    if out.get('page_text'):
        lines.append(f'Page text there: {out["page_text"]}')
    if out.get('controls'):
        lines.append('Buttons, links and fields there: ' + '; '.join(out['controls']))
    if out.get('log'):
        lines.append(f'Log of the test copy:\n{out["log"][-2000:]}')
    shots = []
    if not passed and out.get('screenshot'):
        shots.append(out['screenshot'])
    shots += [s.get('data', '') for s in out.get('shots') or [] if s.get('data')]
    if passed and not shots and out.get('screenshot'):
        shots.append(out['screenshot'])
    return passed, lines, ['data:image/png;base64,' + s for s in shots]


@tool(category=APP_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'])
async def test_app(app_id: str, steps: list[dict] | None = None, name: str = '', save: bool = False,
                   path: str = '', phone: bool = False, data: str = 'fresh', *, parent=None) -> ToolOutcome:
    """Click through a published app like a person would and check it works: open pages, click
    buttons, fill in forms, press keys, then check what shows. Each run uses a throwaway copy of the
    live version with its own data (empty by default), so tests never change what people see. You get
    each step passed or failed with the reason, the script errors and failed requests it caused, and a
    screenshot where it stopped. With save true and a name, a passing test is kept in the app's
    app-tests.json; run Test App with no steps to re-run every saved test after each publish.
    Never asks for approval.

    Args:
        app_id (str): Id or name of the app.
        steps (list[dict]): Steps in order, each an object with one action: {"go": "page"}, {"click": "Add item"}, {"fill": "Title", "with": "Milk"}, {"select": "Priority", "option": "High"}, {"check": "Done"}, {"uncheck": "Done"}, {"press": "Enter", "on": "Title"}, {"hover": "Menu"}, {"see": "text"}, {"not_see": "text"}, {"expect": "#list li", "count": 3} (or "text", "value", "checked", "enabled", "gone": true), {"url": "items?id="}, {"wait": 500 or "selector"}, {"reload": true}, {"js": "expression that must be true"}, {"screenshot": "label"}. Targets are a CSS selector or the visible text, label or placeholder ("css=..." or "text=..." forces one; "nth": 2 picks the second match). Any step can add "timeout" (seconds, default 5) and "dialog" ("accept", "dismiss" or text for a prompt). Empty runs the app's saved tests.
        name (str): Name of the test, e.g. "add an item". With no steps, runs only the saved test with this name.
        save (bool): Keep the steps under name in the app's app-tests.json if they pass.
        path (str): The page to start on, e.g. "settings"; empty for the home page.
        phone (bool): Run on a phone-sized screen (390 wide) instead of a desktop (1280 wide).
        data (str): "fresh" (default) starts the copy with no data; "copy" starts it with a copy of the app's data.
    """
    import json

    user_key = _owner()
    if not user_key:
        return ToolOutcome.failure('no_owner', 'Apps need a signed-in person.')
    try:
        app, role = await store.app_for(app_id, user_key)
    except store.AppError as exc:
        return ToolOutcome.failure(exc.code, exc.message)
    if not store.can_edit(role):
        return ToolOutcome.failure('not_allowed', f'You can use "{app.title}" but not test it. '
                                   'Ask its owner for edit access.')
    if isinstance(steps, str) and steps.strip():
        try:
            steps = json.loads(steps)
        except ValueError as exc:
            return ToolOutcome.failure('bad_steps', f'steps is not valid JSON: {exc}')
    name = ' '.join(str(name or '').split())[:80]
    data = 'copy' if str(data or '').strip().lower() == 'copy' else 'fresh'
    if steps:
        runs = [{'name': name or 'test', 'steps': steps, 'path': path, 'phone': bool(phone)}]
    else:
        try:
            runs = store.saved_tests(app)
        except store.AppError as exc:
            return ToolOutcome.failure(exc.code, exc.message)
        if name:
            runs = [t for t in runs if t['name'].lower() == name.lower()]
        if not runs:
            where = store.tests_file(app)
            return ToolOutcome.failure('no_tests', (f'No saved test called "{name}". ' if name else 'No saved tests. ')
                                       + 'Pass steps, e.g. [{"click": "Add"}, {"see": "Added"}], and save: true '
                                       'with a name to keep a test that passes'
                                       + (f' (it goes in {where}).' if where else '.'))
    entity = [EntityRef(type='hosted_app', id=str(app.id), name=app.title)]
    reports: list[str] = []
    images: list[str] = []
    passed_all = True
    for run in runs:
        try:
            out = await store.test(app, user_key, run['steps'], path=run.get('path', ''), phone=run.get('phone', False),
                                   data=data)
        except store.AppError as exc:
            return ToolOutcome.failure(exc.code, exc.message)
        if not out.get('ok'):
            code = 'bad_steps' if out.get('bad_steps') else 'no_browser' if out.get('no_browser') else 'test_failed'
            return ToolOutcome.failure(code, f'Test "{run["name"]}" could not run: {out.get("error") or "no answer"}')
        size = 'phone' if run.get('phone') else 'desktop'
        label = (f'Test "{run["name"]}" on a {"copy of the data" if data == "copy" else "fresh copy"} of '
                 f'"{app.title}" v{out.get("version") or app.version} ({size})')
        passed, lines, shots = _test_report(app.title, label, out)
        passed_all = passed_all and passed
        reports.append('\n'.join(lines))
        images += shots
    if steps and save:
        if not name:
            reports.append('Not saved: give the test a name to save it.')
        elif not passed_all:
            reports.append('Not saved, because it failed. Fix the app or the steps, run it again with save: true.')
        else:
            try:
                where = store.save_test(app, name, runs[0]['steps'], path=str(path or '').strip().lstrip('/'),
                                        phone=bool(phone))
                reports.append(f'Saved as "{name}" in {where}. Run Test App with no steps to re-run all saved tests.')
            except store.AppError as exc:
                reports.append(f'Not saved: {exc.message}')
    if len(runs) > 1:
        count = sum(1 for r in reports if ': PASSED,' in r.split('\n', 1)[0])
        reports.insert(0, f'{count} of {len(runs)} saved tests passed.')
    from cognitrix.tools.misc import queue_image

    shown = [img for img in images[:4] if queue_image(img)]
    if shown:
        reports.append('Screenshot attached (where the test stopped, or at the end, plus any screenshot steps).'
                       if len(shown) == 1 else f'{len(shown)} screenshots attached.')
    text = '\n\n'.join(reports)
    if not passed_all:
        text += ('\n\nFix the app (or a step that targets the wrong thing), publish again, and run the test again. '
                 "Don't tell the user the app works while a test fails.")
    return ToolOutcome.success(text, entities=entity)
