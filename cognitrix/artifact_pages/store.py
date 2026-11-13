"""Publishing, reading, sharing and listing artifact pages.

Who may do what:

- the owner (the person whose agent made the page) does everything;
- people it's shared with as ``edit`` may comment, send comments to the
  agent and have their own agents update it;
- people it's shared with as ``view`` may open it and comment;
- anyone with the public link may open it, nothing else.

Sources are files under ``<workdir>/artifacts/<page id>/<version>.src``.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cognitrix.artifact_pages.models import (
    EVERYONE,
    FORMATS,
    POWERS,
    ROLE_EDIT,
    ROLE_VIEW,
    SHARE_ROLES,
    ArtifactComment,
    ArtifactConsent,
    ArtifactData,
    ArtifactPage,
    ArtifactVersion,
)

MAX_BYTES = 5 * 1024 * 1024
"""Largest page source (one version)."""
MAX_TITLE = 120
MAX_NOTE = 300
MAX_VERSIONS_KEPT = 100
"""Older version files are removed past this; their rows stay for the history."""
MAX_PAGES_PER_USER = 500

ROLE_OWNER = 'owner'


class PageError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')


def public_links_allowed() -> bool:
    """Public links are on unless the server turns them off."""
    return os.getenv('COGNITRIX_ARTIFACT_PUBLIC_LINKS', '').strip().lower() not in ('off', '0', 'false', 'no')


def root_dir() -> Path:
    from cognitrix.config import settings

    path = Path(settings.workdir) / 'artifacts'
    path.mkdir(parents=True, exist_ok=True)
    return path


def page_dir(page_id: str) -> Path:
    safe = ''.join(ch for ch in str(page_id) if ch.isalnum() or ch in '-_')
    if not safe:
        raise PageError('bad_id', 'Not an artifact id', 404)
    return root_dir() / safe


def _version_file(page_id: str, number: int) -> Path:
    return page_dir(page_id) / f'{int(number)}.src'


# ------------------------------------------------------------------ access

def shares(page: ArtifactPage) -> dict[str, str]:
    raw = page.shared_with if isinstance(page.shared_with, dict) else {}
    return {str(k): str(v) for k, v in raw.items() if str(v) in SHARE_ROLES and str(k)}


def role_of(page: ArtifactPage, user_key: str | None) -> str | None:
    """'owner', 'edit', 'view', or None for someone who can't open it (public links aside)."""
    if not user_key:
        return None
    if str(page.owner_id) == str(user_key):
        return ROLE_OWNER
    given = shares(page)
    roles = {given.get(str(user_key)), given.get(EVERYONE)}
    if ROLE_EDIT in roles:
        return ROLE_EDIT
    return ROLE_VIEW if ROLE_VIEW in roles else None


def can_edit(role: str | None) -> bool:
    return role in (ROLE_OWNER, ROLE_EDIT)


def access_label(page: ArtifactPage) -> str:
    if page.public and public_links_allowed():
        return 'public'
    given = shares(page)
    if EVERYONE in given:
        return 'workspace'
    return 'people' if given else 'private'


async def get_page(page_id: str) -> ArtifactPage | None:
    if not page_id:
        return None
    try:
        return await ArtifactPage.get(str(page_id))
    except Exception:
        return None


async def page_for(page_id: str, user_key: str | None) -> tuple[ArtifactPage, str]:
    """The page and the caller's role, or a 404 that doesn't reveal the page exists."""
    page = await get_page(page_id)
    role = role_of(page, user_key) if page is not None else None
    if page is None or role is None:
        raise PageError('not_found', 'Artifact not found', 404)
    return page, role


async def page_by_public_token(token: str) -> ArtifactPage | None:
    if not token or len(token) < 16 or not public_links_allowed():
        return None
    rows = await ArtifactPage.find({'public_token': str(token)}) or []
    page = rows[0] if rows else None
    return page if page is not None and page.public else None


def viewer_version(page: ArtifactPage, role: str | None) -> int:
    """The version someone sees by default: owners and editors the newest,
    everyone else the one the owner chose to share (or the newest)."""
    if can_edit(role) or not page.share_version:
        return page.version
    return min(page.share_version, page.version)


# ------------------------------------------------------------------ publish

def _clean_title(title: str) -> str:
    text = ' '.join(str(title or '').split())[:MAX_TITLE]
    if not text:
        raise PageError('missing_title', 'Give the artifact a short title')
    return text


def _clean_format(fmt: str) -> str:
    value = str(fmt or 'html').strip().lower()
    value = {'md': 'markdown', 'htm': 'html'}.get(value, value)
    if value not in FORMATS:
        raise PageError('bad_format', "format is 'html' or 'markdown'")
    return value


def _clean_powers(powers: Any) -> list[str]:
    if isinstance(powers, str):
        powers = [p.strip() for p in powers.replace(';', ',').split(',')]
    if not isinstance(powers, list | tuple):
        return []
    out: list[str] = []
    for p in powers:
        p = str(p or '').strip().lower()
        p = {'storage': 'data', 'save': 'data', 'downloads': 'download', 'agent': 'ask',
             'connected_apps': 'apps', 'connectors': 'apps'}.get(p, p)
        if p in POWERS and p not in out:
            out.append(p)
    return out


def _write_source(page_id: str, number: int, content: bytes) -> None:
    folder = page_dir(page_id)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f'.{number}.tmp'
    tmp.write_bytes(content)
    tmp.replace(_version_file(page_id, number))


def _prune(page_id: str, newest: int, keep: int = 0) -> None:
    """Drop sources older than the last ``MAX_VERSIONS_KEPT``, except ``keep``:
    the version the owner holds viewers on, which shared links still show."""
    oldest_kept = newest - MAX_VERSIONS_KEPT
    if oldest_kept <= 0:
        return
    for path in page_dir(page_id).glob('*.src'):
        try:
            if int(path.stem) <= oldest_kept and int(path.stem) != keep:
                path.unlink(missing_ok=True)
        except ValueError:
            continue


async def publish(
    *,
    user_key: str,
    title: str,
    content: str,
    fmt: str = 'html',
    page_id: str = '',
    note: str = '',
    powers: Any = None,
    agent_id: str = '',
    session_id: str = '',
    task_id: str = '',
    run_id: str = '',
) -> tuple[ArtifactPage, bool]:
    """Create a page or add a version to one. Returns (page, created)."""
    if not user_key:
        raise PageError('no_owner', 'Artifacts need a signed-in person to belong to')
    text = str(content or '')
    if not text.strip():
        raise PageError('empty', 'The artifact has no content')
    raw = text.encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise PageError('too_big', f'The page is {len(raw) // 1024} KB; the limit is {MAX_BYTES // (1024 * 1024)} MB. '
                        'Use SVG or CSS instead of embedded images, or summarise the data.')
    clean_title = _clean_title(title)
    clean_format = _clean_format(fmt)
    clean_powers = _clean_powers(powers) if powers is not None else None
    stamp = now()

    page: ArtifactPage | None = None
    if page_id:
        page = await get_page(page_id)
        if page is None or not can_edit(role_of(page, user_key)):
            raise PageError('not_found', f'No artifact {page_id} that you can update. Leave artifact_id empty to make a new one.')
    else:
        # The same task or chat publishing a page with the same title updates
        # it, so a status board a schedule refreshes stays one link.
        for field, value in (('task_id', task_id), ('session_id', session_id)):
            if page is not None or not value:
                continue
            rows = await ArtifactPage.find({'owner_id': user_key, field: value}) or []
            page = next((r for r in rows if r.title.lower() == clean_title.lower()), None)

    digest = hashlib.sha256(raw).hexdigest()
    if page is None:
        mine = await ArtifactPage.find({'owner_id': user_key}) or []
        if len(mine) >= MAX_PAGES_PER_USER:
            raise PageError('too_many', f'You have {MAX_PAGES_PER_USER} artifacts. Delete some on the Artifacts page first.')
        page = ArtifactPage(
            owner_id=user_key, agent_id=agent_id, session_id=session_id, task_id=task_id,
            title=clean_title, format=clean_format, version=1, size_bytes=len(raw),
            powers=clean_powers or [], created=stamp, updated=stamp,
        )
        await page.save()
        _write_source(str(page.id), 1, raw)
        await ArtifactVersion(page_id=str(page.id), number=1, size_bytes=len(raw), sha256=digest,
                              note=str(note or '')[:MAX_NOTE], agent_id=agent_id, session_id=session_id,
                              task_id=task_id, run_id=run_id, created=stamp).save()
        return page, True

    number = int(page.version or 0) + 1
    _write_source(str(page.id), number, raw)
    await ArtifactVersion(page_id=str(page.id), number=number, size_bytes=len(raw), sha256=digest,
                          note=str(note or '')[:MAX_NOTE], agent_id=agent_id, session_id=session_id,
                          task_id=task_id, run_id=run_id, created=stamp).save()
    changes: dict[str, Any] = {
        'version': number, 'title': clean_title, 'format': clean_format,
        'size_bytes': len(raw), 'updated': stamp,
        # A page a task publishes is "live": kept fresh by that task. A chat
        # taking it over again makes it an ordinary page.
        'task_id': task_id or '',
    }
    if clean_powers is not None:
        changes['powers'] = clean_powers
    if session_id and not page.session_id:
        changes['session_id'] = session_id
    await ArtifactPage.update_one({'id': str(page.id)}, changes)
    for key, value in changes.items():
        setattr(page, key, value)
    _prune(str(page.id), number, keep=int(page.share_version or 0))
    return page, False


def read_source(page: ArtifactPage, number: int | None = None) -> str:
    number = int(number or page.version)
    if number < 1 or number > int(page.version):
        raise PageError('no_version', f'Version {number} does not exist', 404)
    path = _version_file(str(page.id), number)
    if not path.is_file():
        raise PageError('version_gone', f'Version {number} is too old and was removed', 404)
    return path.read_text(encoding='utf-8', errors='replace')


async def restore(page: ArtifactPage, number: int, user_key: str) -> ArtifactPage:
    """Publish an older version again as the newest."""
    source = read_source(page, number)
    updated, _ = await publish(user_key=user_key, title=page.title, content=source, fmt=page.format,
                               page_id=str(page.id), note=f'Restored version {number}',
                               agent_id=page.agent_id, session_id=page.session_id)
    return updated


async def delete_page(page: ArtifactPage) -> None:
    pid = str(page.id)
    for model in (ArtifactVersion, ArtifactComment, ArtifactConsent, ArtifactData):
        try:
            await model.delete_many({'page_id': pid})
        except Exception:
            pass
    await ArtifactPage.delete_many({'id': pid})
    shutil.rmtree(page_dir(pid), ignore_errors=True)


async def versions(page: ArtifactPage) -> list[ArtifactVersion]:
    rows = await ArtifactVersion.find({'page_id': str(page.id)}) or []
    return sorted(rows, key=lambda r: int(r.number), reverse=True)


# ------------------------------------------------------------------ sharing

async def set_sharing(page: ArtifactPage, *, people: dict[str, str] | None = None,
                      everyone: str | None = None, public: bool | None = None,
                      share_version: int | None = None) -> ArtifactPage:
    changes: dict[str, Any] = {}
    if people is not None or everyone is not None:
        given = {k: v for k, v in shares(page).items()}
        if people is not None:
            given = {k: v for k, v in given.items() if k == EVERYONE}
            for key, role in people.items():
                if role not in SHARE_ROLES:
                    raise PageError('bad_role', "A role is 'view' or 'edit'")
                if key and key != page.owner_id:
                    given[key] = role
        if everyone is not None:
            given.pop(EVERYONE, None)
            if everyone:
                if everyone not in SHARE_ROLES:
                    raise PageError('bad_role', "A role is 'view' or 'edit'")
                given[EVERYONE] = everyone
        changes['shared_with'] = given
    if public is not None:
        if public and not public_links_allowed():
            raise PageError('public_off', 'Public links are turned off on this server')
        changes['public'] = bool(public)
        if public and not page.public_token:
            changes['public_token'] = secrets.token_urlsafe(24)
    if share_version is not None:
        number = int(share_version)
        if number < 0 or number > page.version:
            raise PageError('no_version', f'Version {number} does not exist')
        changes['share_version'] = number
    if changes:
        await ArtifactPage.update_one({'id': str(page.id)}, changes)
        for key, value in changes.items():
            setattr(page, key, value)
    return page


# ------------------------------------------------------------------ listing

async def visible_pages(user_key: str) -> list[tuple[ArtifactPage, str]]:
    out: list[tuple[ArtifactPage, str]] = []
    for page in await ArtifactPage.all() or []:
        role = role_of(page, user_key)
        if role:
            out.append((page, role))
    out.sort(key=lambda item: item[0].updated or item[0].created, reverse=True)
    return out


async def open_comment_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in await ArtifactComment.find({'thread_id': ''}) or []:
        if not row.resolved:
            counts[row.page_id] = counts.get(row.page_id, 0) + 1
    return counts


async def names() -> tuple[dict[str, str], dict[str, str]]:
    """Display names for people (by user key) and agents (by id)."""
    from cognitrix.agents import Agent
    from cognitrix.agents.access import owner_key
    from cognitrix.models import User

    people: dict[str, str] = {}
    try:
        for user in await User.all() or []:
            people[owner_key(user)] = str(getattr(user, 'name', '') or getattr(user, 'email', '') or 'Someone')
    except Exception:
        pass
    agents: dict[str, str] = {}
    try:
        for agent in await Agent.all() or []:
            agents[str(agent.id)] = str(agent.name)
    except Exception:
        pass
    return people, agents


async def task_info(task_id: str) -> dict[str, Any] | None:
    if not task_id:
        return None
    try:
        from cognitrix.tasks.base import Task

        task = await Task.get(task_id)
    except Exception:
        task = None
    if task is None:
        return None
    schedule = ''
    if getattr(task, 'schedule_enabled', False):
        if task.schedule_interval:
            minutes = max(1, int(task.schedule_interval) // 60)
            schedule = f'every {minutes} min' if minutes < 60 else f'every {minutes // 60} h'
        elif task.schedule_cron:
            schedule = f'cron {task.schedule_cron}'
        elif task.schedule_at:
            schedule = f'once at {task.schedule_at} UTC'
    return {
        'id': str(task.id), 'title': task.title, 'schedule': schedule,
        'next_run_at': getattr(task, 'next_run_at', None) if getattr(task, 'schedule_enabled', False) else None,
    }


def project(page: ArtifactPage, role: str | None, *, people: dict[str, str], agents: dict[str, str],
            comments: int = 0) -> dict[str, Any]:
    return {
        'id': str(page.id),
        'title': page.title,
        'format': page.format,
        'version': page.version,
        'viewer_version': viewer_version(page, role),
        'size_bytes': page.size_bytes,
        'role': role,
        'owner': {'id': page.owner_id, 'name': people.get(page.owner_id, 'Someone')},
        'agent': {'id': page.agent_id, 'name': agents.get(page.agent_id, '')} if page.agent_id else None,
        'session_id': page.session_id if role == ROLE_OWNER else '',
        'task_id': page.task_id if can_edit(role) else '',
        'live': bool(page.task_id),
        'access': access_label(page),
        'pinned': bool(page.pinned) if role == ROLE_OWNER else False,
        'powers': list(page.powers or []),
        'open_comments': comments,
        'created': page.created,
        'updated': page.updated,
    }


# ------------------------------------------------------------------ consent & data

async def consent(page: ArtifactPage, user_key: str) -> ArtifactConsent | None:
    rows = await ArtifactConsent.find({'page_id': str(page.id), 'user_id': user_key}) or []
    return rows[0] if rows else None


async def set_consent(page: ArtifactPage, user_key: str, allowed: list[str]) -> list[str]:
    allowed = [p for p in allowed if p in (page.powers or [])]
    row = await consent(page, user_key)
    if row is None:
        await ArtifactConsent(page_id=str(page.id), user_id=user_key, allowed=allowed, created=now()).save()
    else:
        await ArtifactConsent.update_one({'id': str(row.id)}, {'allowed': allowed})
    return allowed


async def allowed_powers(page: ArtifactPage, user_key: str) -> list[str]:
    row = await consent(page, user_key)
    granted = set(row.allowed if row else [])
    return [p for p in (page.powers or []) if p in granted]


MAX_DATA_KEYS = 200
MAX_DATA_VALUE = 100 * 1024
MAX_DATA_TOTAL = 2 * 1024 * 1024


def _data_owner(user_key: str, scope: str) -> str:
    return user_key if scope == 'mine' else ''


async def data_get(page: ArtifactPage, user_key: str, scope: str, key: str | None) -> Any:
    rows = await ArtifactData.find({'page_id': str(page.id), 'user_id': _data_owner(user_key, scope)}) or []
    if key is None:
        return {r.key: r.value for r in rows}
    row = next((r for r in rows if r.key == key), None)
    return row.value if row else None


async def data_set(page: ArtifactPage, user_key: str, scope: str, key: str, value: str | None) -> None:
    key = str(key or '')[:200]
    if not key:
        raise PageError('bad_key', 'A key is needed')
    owner = _data_owner(user_key, scope)
    rows = await ArtifactData.find({'page_id': str(page.id), 'user_id': owner}) or []
    row = next((r for r in rows if r.key == key), None)
    if value is None:
        if row is not None:
            await ArtifactData.delete_many({'id': str(row.id)})
        return
    if len(value.encode('utf-8')) > MAX_DATA_VALUE:
        raise PageError('too_big', f'A value can be at most {MAX_DATA_VALUE // 1024} KB')
    total = sum(len((r.value or '').encode('utf-8')) for r in rows if r is not row) + len(value.encode('utf-8'))
    if total > MAX_DATA_TOTAL:
        raise PageError('too_big', 'This page has saved as much data as it may')
    if row is None:
        if len(rows) >= MAX_DATA_KEYS:
            raise PageError('too_many', f'A page can save at most {MAX_DATA_KEYS} values')
        await ArtifactData(page_id=str(page.id), user_id=owner, key=key, value=value, updated=now()).save()
    else:
        await ArtifactData.update_one({'id': str(row.id)}, {'value': value, 'updated': now()})


# ------------------------------------------------------------------ files agents wrote

DOC_EXTENSIONS = {
    '.md': 'text/markdown', '.markdown': 'text/markdown', '.txt': 'text/plain', '.html': 'text/html',
    '.htm': 'text/html', '.csv': 'text/csv', '.tsv': 'text/tab-separated-values', '.json': 'application/json',
    '.svg': 'image/svg+xml', '.xml': 'application/xml', '.yaml': 'text/yaml', '.yml': 'text/yaml',
    '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.rtf': 'application/rtf', '.tex': 'text/x-tex',
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_USER = 1000


async def record_written_file(*, owner_id: str, path: str, agent_id: str = '', session_id: str = '',
                              task_id: str = '') -> None:
    """Remember a document an agent wrote, so it shows on the Artifacts page.
    Code and config files aren't listed; only document-like files."""
    from cognitrix.artifact_pages.models import ArtifactFile

    if not owner_id or not path:
        return
    file = Path(path)
    mime = DOC_EXTENSIONS.get(file.suffix.lower())
    if mime is None:
        return
    try:
        size = file.stat().st_size
    except OSError:
        return
    stamp = now()
    rows = await ArtifactFile.find({'owner_id': owner_id, 'path': str(file)}) or []
    if rows:
        await ArtifactFile.update_one({'id': str(rows[0].id)}, {
            'size_bytes': size, 'updated': stamp, 'agent_id': agent_id or rows[0].agent_id,
            'session_id': session_id or rows[0].session_id, 'task_id': task_id or rows[0].task_id,
        })
        return
    if len(await ArtifactFile.find({'owner_id': owner_id}) or []) >= MAX_FILES_PER_USER:
        return
    await ArtifactFile(owner_id=owner_id, agent_id=agent_id, session_id=session_id, task_id=task_id,
                       path=str(file), name=file.name, mime_type=mime, size_bytes=size,
                       created=stamp, updated=stamp).save()


WRITTEN_PREFIX = 'Written to file: '


def written_path(tool_name: str | None, result: Any) -> str | None:
    """The file a successful Write call created or replaced, from its result text."""
    if str(tool_name or '').strip().lower() != 'write' or not isinstance(result, str):
        return None
    first = result.strip().splitlines()[0] if result.strip() else ''
    if not first.startswith(WRITTEN_PREFIX):
        return None
    return first[len(WRITTEN_PREFIX):].strip() or None
