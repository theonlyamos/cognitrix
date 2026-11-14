"""Publishing, sharing, waking and listing apps.

Who may do what (same roles as artifact pages):

- the owner does everything;
- people it's shared with as ``edit`` may open it and have their agents publish new versions;
- people it's shared with as ``view`` may open it.

Apps are never public: everyone who opens one is signed in to this Cognitrix,
and the app is told who they are (``X-Cognitrix-User-*`` headers).
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt

from cognitrix.artifact_pages.models import EVERYONE, SHARE_ROLES
from cognitrix.artifact_pages.store import ROLE_OWNER, can_edit, names, role_of, shares, task_info  # noqa: F401
from cognitrix.computer.sandbox.apps import AppHostError, pack_folder
from cognitrix.hosted_apps.models import HostedApp, HostedAppVersion
from cognitrix.hosted_apps.runner import RunnerError, default_runtime, runner_for

logger = logging.getLogger('cognitrix.apps')

MAX_APPS_PER_USER = 100
MAX_TITLE = 80
MAX_NOTE = 300
VISIT_DAYS = 30
TOKEN_HOURS = 12
_AUDIENCE = 'cognitrix-app'
API_SCOPES = ['read', 'chat', 'run']
"""What an app's own Cognitrix API key may do (no 'write': it can't change agents or settings)."""
RESERVED = {'new', 'open', 'api', 'admin', 'static', 'assets'}


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')


def today() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d')


# ------------------------------------------------------------------ lookups

async def get_app(app_id: str) -> HostedApp | None:
    if not app_id:
        return None
    try:
        return await HostedApp.get(str(app_id))
    except Exception:
        return None


async def by_slug(slug: str) -> HostedApp | None:
    rows = await HostedApp.find({'slug': str(slug or '')}) or []
    return rows[0] if rows else None


async def find(ref: str) -> HostedApp | None:
    """An app by id or slug."""
    ref = str(ref or '').strip()
    return await get_app(ref) or await by_slug(ref.lower())


async def app_for(ref: str, user_key: str | None) -> tuple[HostedApp, str]:
    app = await find(ref)
    role = role_of(app, user_key) if app is not None else None
    if app is None or role is None:
        raise AppError('not_found', 'App not found', 404)
    return app, role


async def visible(user_key: str) -> list[tuple[HostedApp, str]]:
    out = []
    for app in await HostedApp.all() or []:
        role = role_of(app, user_key)
        if role:
            out.append((app, role))
    out.sort(key=lambda item: item[0].updated or item[0].created, reverse=True)
    return out


async def versions(app: HostedApp) -> list[HostedAppVersion]:
    rows = await HostedAppVersion.find({'app_id': str(app.id)}) or []
    return sorted(rows, key=lambda r: int(r.number), reverse=True)


async def _set(app: HostedApp, **changes: Any) -> HostedApp:
    if changes:
        await HostedApp.update_one({'id': str(app.id)}, changes)
        for key, value in changes.items():
            setattr(app, key, value)
    return app


async def _set_version(row: HostedAppVersion, **changes: Any) -> None:
    await HostedAppVersion.update_one({'id': str(row.id)}, changes)
    for key, value in changes.items():
        setattr(row, key, value)


# ------------------------------------------------------------------ naming

def _slugify(title: str) -> str:
    text = re.sub(r'[^a-z0-9]+', '-', str(title or '').lower()).strip('-')[:40].strip('-')
    return text or 'app'


async def new_slug(title: str) -> str:
    base = _slugify(title)
    if base in RESERVED:
        base = f'{base}-app'
    slug = base
    while await by_slug(slug) is not None:
        slug = f'{base}-{secrets.token_hex(2)}'
    return slug


def _clean_title(title: str) -> str:
    text = ' '.join(str(title or '').split())[:MAX_TITLE]
    if not text:
        raise AppError('no_title', 'Give the app a short name.')
    return text


# ------------------------------------------------------------------ the engine's address, for apps

_engine_url = ''


def remember_engine_url(url: str) -> None:
    """Where this engine answers, learnt from a request (apps call the API here)."""
    global _engine_url
    if url and not _engine_url:
        _engine_url = url.rstrip('/')


def api_url_for(runtime: str) -> str:
    import os

    configured = os.getenv('COGNITRIX_APPS_API_URL', '').strip().rstrip('/')
    if configured:
        return configured
    url = _engine_url or 'http://127.0.0.1:8000'
    if runtime == 'sandbox':
        url = re.sub(r'//(localhost|127\.0\.0\.1|0\.0\.0\.0)(?=[:/]|$)', '//host.docker.internal', url)
    return url


async def _ensure_api_key(app: HostedApp) -> tuple[str, str]:
    """A Cognitrix API key for the app (made once; shown to the app only)."""
    from cognitrix.models.api_key import APIKey, generate_secret, hash_secret

    secret = generate_secret()
    key = APIKey(name=f'App: {app.title}'[:80], user_id=app.owner_id, key_hash=hash_secret(secret),
                 prefix=secret[:10], scopes=list(API_SCOPES))
    await key.save()
    return str(key.id), secret


async def _revoke_api_key(app: HostedApp, key_id: str = '') -> None:
    key_id = key_id or app.api_key_id
    if not key_id:
        return
    from cognitrix.models.api_key import APIKey

    try:
        await APIKey.update_one({'id': key_id}, {'revoked': True})
    except Exception:
        logger.debug('could not revoke app key', exc_info=True)


# ------------------------------------------------------------------ publishing

_deploy_locks: dict[str, asyncio.Lock] = {}


def _clean_env(env: Any) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    out = {}
    for key, value in list(env.items())[:50]:
        key = str(key).strip()
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key) and not key.startswith('COGNITRIX_'):
            out[key] = str(value)[:2000]
    return out


async def publish(
    *,
    user_key: str,
    title: str,
    folder: str,
    command: str,
    install: str = '',
    note: str = '',
    app_id: str = '',
    description: str = '',
    port: int = 0,
    env: dict | None = None,
    branch: str = '',
    use_cognitrix_api: bool = False,
    agent_id: str = '',
    session_id: str = '',
    task_id: str = '',
    runtime: str | None = None,
) -> tuple[HostedApp, HostedAppVersion, bool]:
    """Create an app or add a version to one, and start it. Returns (app, version, created).
    A version that fails to start is recorded as failed; the live one keeps serving."""
    if not user_key:
        raise AppError('no_owner', 'Apps need a signed-in person to belong to.')
    root = Path(str(folder or '')).expanduser()
    if not root.is_dir():
        raise AppError('no_folder', f'{folder} is not a folder on this machine. Write the app\'s files there first.')
    command = ' '.join(str(command or '').split())
    if not command:
        raise AppError('no_command', 'A start command is needed, e.g. "python3 app.py" or "npm start".')
    clean_title = _clean_title(title)
    stamp = now()

    app: HostedApp | None = None
    if app_id:
        app = await find(app_id)
        if app is None or not can_edit(role_of(app, user_key)):
            raise AppError('not_found', f'No app {app_id} that you can update. Leave app_id empty to make a new one.')
    elif session_id or task_id:
        # Publishing the same title again from the same chat or task updates it.
        for field_name, value in (('task_id', task_id), ('session_id', session_id)):
            if app is not None or not value:
                continue
            rows = await HostedApp.find({'owner_id': user_key, field_name: value}) or []
            app = next((r for r in rows if r.title.lower() == clean_title.lower()), None)

    created = app is None
    if created:
        chosen = runtime or default_runtime()
        if chosen is None:
            raise AppError('apps_off', 'Apps are turned off here (turn the sandboxed computer on in Settings, '
                           'or allow apps on this machine).')
        mine = await HostedApp.find({'owner_id': user_key}) or []
        if len(mine) >= MAX_APPS_PER_USER:
            raise AppError('too_many', f'You have {MAX_APPS_PER_USER} apps. Delete some first.')
        app = HostedApp(owner_id=user_key, agent_id=agent_id, session_id=session_id, task_id=task_id,
                        slug=await new_slug(clean_title), title=clean_title, description=str(description or '')[:500],
                        runtime=chosen, status='building', created=stamp, updated=stamp)
        await app.save()

    elif app.runtime == 'local' and default_runtime() == 'sandbox':
        # The sandboxed computer was turned on since: the app moves into it.
        try:
            await asyncio.to_thread(lambda: runner_for('local', app.owner_id).remove(app.slug))
        except RunnerError:
            pass
        await _set(app, runtime='sandbox', version=0, status='building')

    lock = _deploy_locks.setdefault(str(app.id), asyncio.Lock())
    async with lock:
        number = max(int(app.latest or 0), int(app.version or 0)) + 1
        row = HostedAppVersion(app_id=str(app.id), number=number, status='building', note=str(note or '')[:MAX_NOTE],
                               branch=str(branch or '')[:120], agent_id=agent_id, session_id=session_id,
                               task_id=task_id, created=stamp)
        await row.save()
        changes: dict[str, Any] = {
            'latest': number, 'title': clean_title, 'folder': str(root), 'command': command,
            'install': ' '.join(str(install or '').split()), 'port': int(port or 0), 'env': _clean_env(env),
            'branch': str(branch or '')[:120], 'updated': stamp,
        }
        if description:
            changes['description'] = str(description)[:500]
        if not app.version:
            changes['status'] = 'building'
        if task_id:
            changes['task_id'] = task_id
        if session_id and not app.session_id:
            changes['session_id'] = session_id
        await _set(app, **changes)

        run_env = dict(app.env)
        run_env['APP_TITLE'] = app.title
        new_key = ''
        if use_cognitrix_api or app.api_key_id:
            # A fresh key per version; the old one is revoked once the new version is live.
            new_key, secret = await _ensure_api_key(app)
            run_env.update({'COGNITRIX_API_URL': api_url_for(app.runtime), 'COGNITRIX_API_KEY': secret})

        try:
            archive = await asyncio.to_thread(pack_folder, str(root))
            runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
            out = await asyncio.to_thread(runner.deploy, app.slug, number, archive, app.command, app.install,
                                          run_env, int(app.port or 0))
        except (AppHostError, RunnerError) as exc:
            out = {'ok': False, 'error': exc.message, 'log': ''}

        if out.get('ok'):
            previous = int(app.version or 0)
            await _set_version(row, status='live', log=str(out.get('log') or '')[-4000:])
            if previous:
                for old in await HostedAppVersion.find({'app_id': str(app.id), 'status': 'live'}) or []:
                    if int(old.number) != number:
                        await _set_version(old, status='ok')
            await _set(app, version=number, status='online', last_error='', updated=now())
            if new_key:
                await _revoke_api_key(app)
                await _set(app, api_key_id=new_key)
            touch(app.slug)
        else:
            error = str(out.get('error') or 'It did not start.')
            await _set_version(row, status='failed', error=error[:1000], log=str(out.get('log') or '')[-6000:])
            await _set(app, status='failed' if not app.version else app.status, last_error=error[:1000],
                       updated=now())
            if new_key:
                await _revoke_api_key(app, new_key)
        return app, row, created


async def promote(app: HostedApp, number: int) -> HostedApp:
    """Make a kept version live again."""
    rows = {int(r.number): r for r in await versions(app)}
    row = rows.get(int(number))
    if row is None or row.status == 'failed':
        raise AppError('no_version', f'Version {number} never ran, so it can\'t go live.')
    try:
        runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
        out = await asyncio.to_thread(runner.promote, app.slug, int(number))
    except RunnerError as exc:
        raise AppError(exc.code, exc.message) from None
    if not out.get('ok'):
        raise AppError('failed', str(out.get('error') or 'That version did not start.'))
    for old in rows.values():
        if old.status == 'live':
            await _set_version(old, status='ok')
    await _set_version(row, status='live')
    await _set(app, version=int(number), status='online', updated=now())
    touch(app.slug)
    return app


async def delete_app(app: HostedApp) -> None:
    try:
        runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
        await asyncio.to_thread(runner.remove, app.slug)
    except RunnerError:
        logger.info('app %s: runner not reachable while deleting; files may stay behind', app.slug)
    await _revoke_api_key(app)
    await HostedAppVersion.delete_many({'app_id': str(app.id)})
    await HostedApp.delete_many({'id': str(app.id)})


async def logs(app: HostedApp, lines: int = 200, version: int = 0) -> str:
    try:
        runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
        return await asyncio.to_thread(runner.logs, app.slug, lines, version)
    except RunnerError as exc:
        return f'(logs unavailable: {exc.message})'


async def set_sharing(app: HostedApp, *, people: dict[str, str] | None = None,
                      everyone: str | None = None) -> HostedApp:
    given = dict(shares(app))
    if people is not None:
        given = {k: v for k, v in given.items() if k == EVERYONE}
        for key, role in people.items():
            if role not in SHARE_ROLES:
                raise AppError('bad_role', "A role is 'view' or 'edit'")
            if key and key != app.owner_id:
                given[key] = role
    if everyone is not None:
        given.pop(EVERYONE, None)
        if everyone:
            if everyone not in SHARE_ROLES:
                raise AppError('bad_role', "A role is 'view' or 'edit'")
            given[EVERYONE] = everyone
    return await _set(app, shared_with=given)


def access_label(app: HostedApp) -> str:
    given = shares(app)
    if EVERYONE in given:
        return 'workspace'
    return 'people' if given else 'private'


# ------------------------------------------------------------------ waking and sleeping

_last_active: dict[str, float] = {}
_waking: dict[str, asyncio.Task] = {}


def touch(slug: str) -> None:
    _last_active[slug] = time.time()


async def wake(app: HostedApp) -> bool:
    """Start an asleep app (one start at a time per app). True when it's online."""
    if app.status == 'online':
        return True
    task = _waking.get(app.slug)
    if task is None or task.done():
        async def _go() -> bool:
            try:
                runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
                out = await asyncio.to_thread(runner.start, app.slug)
            except RunnerError as exc:
                await _set(app, last_error=exc.message)
                return False
            if out.get('ok'):
                await _set(app, status='online')
                touch(app.slug)
                return True
            await _set(app, last_error=str(out.get('error') or 'It did not start.')[:1000])
            return False

        task = _waking[app.slug] = asyncio.create_task(_go())
    return await asyncio.shield(task)


def waking(slug: str) -> bool:
    task = _waking.get(slug)
    return task is not None and not task.done()


async def sleep_idle(now_ts: float | None = None) -> list[str]:
    """Put apps with no visitors for their sleep_after minutes to sleep."""
    now_ts = now_ts or time.time()
    slept = []
    for app in await HostedApp.find({'status': 'online'}) or []:
        minutes = int(app.sleep_after or 0)
        if minutes <= 0:
            continue
        last = _last_active.setdefault(app.slug, now_ts)  # first seen now: give it a full period
        if now_ts - last < minutes * 60:
            continue
        try:
            runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
            await asyncio.to_thread(runner.stop, app.slug)
        except RunnerError:
            continue
        await _set(app, status='asleep')
        slept.append(app.slug)
    return slept


async def sleeper_loop(interval: float = 60) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await sleep_idle()
        except Exception:
            logger.exception('putting idle apps to sleep failed')


async def record_visit(app: HostedApp) -> None:
    day = today()
    counts = {k: int(v) for k, v in (app.visits or {}).items()
              if k >= (datetime.now(UTC) - timedelta(days=VISIT_DAYS)).strftime('%Y-%m-%d')}
    counts[day] = counts.get(day, 0) + 1
    await _set(app, visits=counts, last_visit=now())


def visits_last(app: HostedApp, days: int = 7) -> list[int]:
    start = datetime.now(UTC).date()
    counts = app.visits or {}
    return [int(counts.get((start - timedelta(days=offset)).strftime('%Y-%m-%d'), 0))
            for offset in range(days - 1, -1, -1)]


# ------------------------------------------------------------------ links

def _key() -> str:
    from cognitrix.common.constants import JWT_SECRET_KEY

    return f'{JWT_SECRET_KEY}:hosted-app'


def open_token(slug: str, viewer: str, thumb: bool = False) -> str:
    """A viewer's way in. A ``thumb`` token is for the small live picture on the
    Apps page: it never wakes the app, counts a visit or keeps it awake."""
    from cognitrix.common.constants import JWT_ALGORITHM

    payload = {'s': slug, 'u': str(viewer), 'aud': _AUDIENCE,
               'exp': datetime.now(UTC) + timedelta(hours=TOKEN_HOURS)}
    if thumb:
        payload['t'] = 1
    return jwt.encode(payload, _key(), algorithm=JWT_ALGORITHM)


def read_open_token(token: str, slug: str) -> tuple[str, bool] | None:
    """(viewer's user key, is a thumbnail token) when the token is good for this app."""
    from cognitrix.common.constants import JWT_ALGORITHM

    try:
        data = jwt.decode(token, _key(), algorithms=[JWT_ALGORITHM], audience=_AUDIENCE)
    except jwt.PyJWTError:
        return None
    if data.get('s') != slug or not data.get('u'):
        return None
    return str(data['u']), bool(data.get('t'))


def app_path(slug: str, token: str) -> str:
    return f'/a/{slug}/~{token}/'


# ------------------------------------------------------------------ output

def project(app: HostedApp, role: str | None, *, people: dict[str, str], agents: dict[str, str]) -> dict[str, Any]:
    owner = role == ROLE_OWNER
    return {
        'id': str(app.id),
        'slug': app.slug,
        'title': app.title,
        'description': app.description,
        'status': 'building' if waking(app.slug) and app.status == 'asleep' else app.status,
        'version': app.version,
        'latest': app.latest,
        'last_error': app.last_error if can_edit(role) else '',
        'runtime': app.runtime,
        'branch': app.branch,
        'role': role,
        'owner': {'id': app.owner_id, 'name': people.get(app.owner_id, 'Someone')},
        'agent': {'id': app.agent_id, 'name': agents.get(app.agent_id, '')} if app.agent_id else None,
        'session_id': app.session_id if owner else '',
        'task_id': app.task_id if can_edit(role) else '',
        'access': access_label(app),
        'shared_with': shares(app) if owner else {},
        'pinned': bool(app.pinned) if owner else False,
        'sleep_after': app.sleep_after,
        'visits_7d': visits_last(app, 7),
        'uses_api': bool(app.api_key_id),
        'command': app.command if can_edit(role) else '',
        'install': app.install if can_edit(role) else '',
        'folder': app.folder if owner else '',
        'link': f'/apps/open/{app.slug}',
        'created': app.created,
        'updated': app.updated,
    }


def project_version(row: HostedAppVersion, agents: dict[str, str]) -> dict[str, Any]:
    return {
        'number': row.number, 'status': row.status, 'note': row.note, 'error': row.error, 'log': row.log,
        'branch': row.branch, 'agent': {'id': row.agent_id, 'name': agents.get(row.agent_id, '')} if row.agent_id else None,
        'created': row.created,
    }
