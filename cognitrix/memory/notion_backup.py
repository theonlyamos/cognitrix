"""Keep a copy of a person's saved memories in their Notion, and bring it back.

After every finished tidy (dream mode, nightly or Tidy now) the memories and
the "About you" summary are written to one Notion page in the person's own
workspace, through their Notion connected app. The same page is replaced each
time, never duplicated: its id is kept in ``NotionBackup``, and when that row
is gone (a fresh install) the page is found again by its title.

Restoring reads that page back. Memories that were approved before come back
approved; agent-only notes go to the agent with the same name, and the ones
whose agent or project no longer exists come back as proposals the person
approves in Settings › Memory, so they never reach the wrong agent.

Nothing here raises to the caller: without a Notion connection, or when
Notion fails, the status row says why and Settings › Memory shows it.
Memories that look like a password, key or token are never written out.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from odbms import Model
from pydantic import field_validator

from cognitrix.memory import saved

logger = logging.getLogger('cognitrix.log')

PAGE_TITLE = 'Cognitrix memory backup'
FORMAT = 'cognitrix-memory-backup'
NOTION_HOST = 'mcp.notion.com'

OK, FAILED, NOT_CONNECTED, RESTORE_FIRST, WAITING = 'ok', 'failed', 'not_connected', 'restore_first', 'waiting_review'

NOT_CONNECTED_MESSAGE = 'Notion is not connected. Connect it in Settings › Connected apps to back up your memory.'


class NotionBackup(Model):
    """One row per person: where their backup page is and how the last backup went."""
    user_id: str
    enabled: bool = True
    """Back up after every tidy. Off: only the Back up now button writes."""
    page_id: str = ''
    page_url: str = ''
    status: str = ''
    """'' (never ran), 'ok', 'failed', 'not_connected' or 'restore_first' (Notion holds a
    bigger backup from another install that a tidy here would have replaced)."""
    message: str = ''
    last_at: str = ''
    """UTC time of the last attempt."""
    last_ok: str = ''
    """UTC time of the last backup that worked."""
    count: int = 0
    owned: bool = False
    """This install has written ``page_id`` itself. Until then a nightly tidy never
    overwrites that page without checking it first (it may hold another install's backup)."""
    restored: bool = False
    """The person restored from ``page_id``. Its notes that came back as proposals keep
    the page as it is until they're answered."""

    @field_validator('page_id', 'page_url', 'status', 'message', 'last_at', 'last_ok', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('enabled', mode='before')
    @classmethod
    def _enabled_default(cls, value):
        return True if value is None else bool(value)

    @field_validator('owned', 'restored', mode='before')
    @classmethod
    def _flag_default(cls, value):
        return bool(value) if value is not None else False

    @field_validator('count', mode='before')
    @classmethod
    def _count_default(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


MODELS = (NotionBackup,)


class BackupError(RuntimeError):
    """A backup or restore that couldn't be done; the message is safe to show."""

    def __init__(self, message: str, status: str = FAILED):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- state

async def get_state(user_id: str) -> NotionBackup:
    try:
        row = await NotionBackup.find_one({'user_id': str(user_id)})
    except Exception as exc:
        if 'no such table' not in str(exc).lower():
            raise
        row = None  # the first save creates the table
    if row is None:
        row = NotionBackup(user_id=str(user_id))
        await row.save()
        row = await NotionBackup.find_one({'user_id': str(user_id)}) or row
    return row


async def _set(row: NotionBackup, **changes: Any) -> NotionBackup:
    await NotionBackup.update_one({'id': row.id}, changes)
    for key, value in changes.items():
        setattr(row, key, value)
    return row


async def set_enabled(user_id: str, enabled: bool) -> NotionBackup:
    return await _set(await get_state(user_id), enabled=bool(enabled))


# ---------------------------------------------------------------- the Notion connection

def is_notion(config: Any) -> bool:
    return (getattr(config, 'catalog_id', None) == 'notion'
            or NOTION_HOST in str(getattr(config, 'url', '') or ''))


async def find_connection(user_id: str) -> Any | None:
    """The person's own Notion connection, if they have one switched on."""
    from cognitrix.mcp import registry

    try:
        servers = await registry.visible_servers(user_id)
    except Exception:
        logger.debug('Could not list connected apps', exc_info=True)
        return None
    mine = registry._owner(user_id)
    for config in servers:
        if is_notion(config) and getattr(config, 'enabled', True) and registry._owner(config.owner_id) == mine:
            return config
    return None


class _Notion:
    """The few Notion MCP tools a backup needs, whatever this server version calls them."""

    def __init__(self, client: Any, config: Any, names: set[str]):
        self.client = client
        self.config = config
        self.names = names

    def _name(self, base: str) -> str:
        for name in (f'notion-{base}', base, f'notion_{base.replace("-", "_")}', base.replace('-', '_')):
            if name in self.names:
                return name
        raise BackupError(f"The Notion connection has no '{base}' tool. Reconnect Notion and try again.")

    async def call(self, base: str, arguments: dict[str, Any]) -> str:
        from cognitrix.mcp import apps
        from cognitrix.tools.mcp import _content_to_text

        result = await apps.call_tool(self.client, self.config, self._name(base), arguments)
        if result is None:
            raise BackupError("Notion didn't answer. Try again later.")
        text = _content_to_text(result)
        text = text if isinstance(text, str) else json.dumps(text, default=str)
        failed = bool(getattr(getattr(result, 'result', None), 'isError', False))
        if failed or text.lstrip().startswith('Error:'):
            # The answer can hold page content (memories), so only its size is logged.
            logger.warning('Notion %s failed (%d characters of error text)', base, len(text))
            raise BackupError('Notion could not save or read the backup page. Try again later.')
        return text


async def _open(user_id: str) -> _Notion:
    from cognitrix.mcp import apps, registry
    from cognitrix.mcp.client import get_dynamic_client

    config = await find_connection(user_id)
    if config is None:
        raise BackupError(NOT_CONNECTED_MESSAGE, NOT_CONNECTED)
    client = await get_dynamic_client()
    problem = await registry.prepare(client, config)
    if problem:
        logger.info('Notion backup could not connect to the Notion app')
        raise BackupError('Notion needs you to sign in again. Press Connect next to Notion in Settings › Connected apps.',
                          NOT_CONNECTED)
    names = {str(t.get('name') or '') for t in await apps.list_tools(client, config)}
    return _Notion(client, config, names)


# ---------------------------------------------------------------- what goes in the page

async def _account(user_id: str) -> str:
    try:
        from cognitrix.models import User

        user = await User.get(user_id)
        return str(getattr(user, 'email', '') or '').strip().lower() if user else ''
    except Exception:
        return ''


async def _names(user_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """Agent and project names by id, so a restore can find them again by name."""
    agents: dict[str, str] = {}
    projects: dict[str, str] = {}
    try:
        from cognitrix.agents import access

        agents = {str(a.id): a.name for a in await access.visible_agents(user_id)}
    except Exception:
        logger.debug('Could not list agents for the memory backup', exc_info=True)
    try:
        from cognitrix.projects.models import Project

        projects = {str(p.id): p.name for p in await Project.find({'user_id': str(user_id)})}
    except Exception:
        pass
    return agents, projects


async def snapshot(user_id: str) -> dict[str, Any]:
    """Everything the backup holds. Pending proposals and secret-looking notes are left out."""
    agents, projects = await _names(user_id)
    items = []
    skipped = 0
    for row in await saved.list_memories(user_id):
        if row.pending:
            continue
        if saved.looks_secret(row.content):
            skipped += 1
            continue
        item: dict[str, Any] = {'content': row.content, 'scope': row.scope}
        if row.scope == 'agent':
            item['agent'] = agents.get(row.agent_id, '')
        if row.scope == 'project':
            item['project'] = projects.get(row.project_id, '')
        for key in ('kind', 'valid_until'):
            if getattr(row, key, ''):
                item[key] = getattr(row, key)
        if row.pinned:
            item['pinned'] = True
        item['created'] = row.created
        items.append(item)
    profile = await saved.get_profile(user_id)
    about = profile.text if profile and profile.text and not saved.looks_secret(profile.text) else ''
    return {
        'format': FORMAT,
        'version': 1,
        'account': await _account(user_id),
        'saved': saved.now_str(),
        'about_you': about,
        'about_you_locked': bool(profile and profile.locked),
        'memories': items,
        'left_out': skipped,
    }


def render(data: dict[str, Any]) -> str:
    """The page: a readable list on top, the exact copy the app restores from below."""
    items = data.get('memories') or []
    lines = [
        f"What your Cognitrix agents remember, saved {data.get('saved', '')} UTC after a memory tidy. "
        'This page is replaced after every tidy. To bring it back after reinstalling, connect Notion and '
        'press Restore from Notion in Settings › Memory.',
        '',
    ]
    if data.get('about_you'):
        lines += ['## About you', '', str(data['about_you']).strip(), '']
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get('scope') == 'agent':
            key = f"Only {item.get('agent') or 'one agent'}"
        elif item.get('scope') == 'project':
            key = f"Only the {item.get('project') or 'one'} project"
        else:
            key = 'All agents'
        groups.setdefault(key, []).append(item)
    if not items:
        lines += ['Nothing saved yet.', '']
    for title in sorted(groups, key=lambda k: (k != 'All agents', k)):
        lines += [f'## {title}', '']
        lines += [f"- {_one_line(i['content'])}" + (' (pinned)' if i.get('pinned') else '') for i in groups[title]]
        lines.append('')
    lines += ['## Backup data', '', 'Cognitrix restores from this block. Please leave it as it is.', '',
              '```json', json.dumps(data, ensure_ascii=False, indent=1), '```']
    return '\n'.join(lines)


def _one_line(text: str) -> str:
    return ' '.join(str(text).split())


_CODE = re.compile(r'```(?:json)?\s*\n(.*?)\n\s*```', re.DOTALL)


def _page_text(raw: str) -> str:
    """The page's text from a fetch result, which may be JSON around Notion's markup."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(data, dict):
        for key in ('text', 'content', 'markdown'):
            if isinstance(data.get(key), str):
                return data[key]
    return raw


def parse(raw: str) -> dict[str, Any] | None:
    """Read a backup page back. None when it holds no backup."""
    text = _page_text(raw)
    for block in _CODE.findall(text):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('format') == FORMAT and isinstance(data.get('memories'), list):
            return data
    # Someone tidied the data block away: the readable list still holds the notes.
    from cognitrix.memory.importer import split_lines

    readable = text.split('## Backup data')[0]
    memories, about, section = [], [], ''
    for line in readable.splitlines():
        stripped = line.strip()
        if stripped.startswith('## '):
            section = stripped[3:].strip()
            continue
        if section == 'About you':
            if stripped:
                about.append(stripped)
        elif section and stripped.startswith(('-', '*', '•')):
            for content in split_lines(stripped):
                pinned = content.endswith(' (pinned)')
                content = content.removesuffix(' (pinned)')
                memories.append({'content': content, 'scope': 'global' if section == 'All agents' else 'other',
                                 'pinned': pinned})
    if not memories and not about:
        return None
    return {'format': FORMAT, 'version': 1, 'about_you': ' '.join(about), 'memories': memories, 'account': ''}


_ID = re.compile(r'([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})', re.IGNORECASE)
_URL = re.compile(r'https://(?:www\.)?notion\.so/[^\s"\'<>)\]]+')


def _page_ref(text: str) -> tuple[str, str]:
    """(page id, page url) from a create or search result."""
    url_match = _URL.search(text)
    url = url_match.group(0) if url_match else ''
    id_match = _ID.search(url) or _ID.search(text)
    return (id_match.group(1).replace('-', '') if id_match else ''), url


def _search_hits(raw: str, title: str = PAGE_TITLE) -> list[tuple[str, str]]:
    """Pages with this title (the backup page), as (id, url), from a search result."""
    hits: list[tuple[str, str]] = []
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    results = data.get('results') if isinstance(data, dict) else data if isinstance(data, list) else None
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            if str(item.get('title') or '').strip() != title:
                continue
            page_id, url = _page_ref(json.dumps(item))
            page_id = str(item.get('id') or page_id).replace('-', '')
            if page_id:
                hits.append((page_id, str(item.get('url') or url)))
        return hits
    # Unknown shape: take any page id next to the title.
    for chunk in raw.split(title)[1:]:
        page_id, url = _page_ref(chunk[:600])
        if page_id:
            hits.append((page_id, url))
    return hits


async def _find_page(notion: _Notion, account: str) -> tuple[str, str, dict[str, Any] | None]:
    """The person's backup page in Notion: (id, url, its data), or ('', '', None)."""
    raw = await notion.call('search', {'query': PAGE_TITLE})
    fallback: tuple[str, str, dict[str, Any] | None] = ('', '', None)
    for page_id, url in _search_hits(raw)[:5]:
        try:
            data = parse(await notion.call('fetch', {'id': page_id}))
        except BackupError:
            continue
        if data is None:
            continue
        if account and data.get('account') == account:
            return page_id, url, data
        if not fallback[0]:
            # It's in the person's own Notion, so a backup made under another sign-in is still theirs.
            fallback = (page_id, url, data)
    return fallback


# ---------------------------------------------------------------- back up

async def backup(user_id: str, *, trigger: str = 'tidy') -> NotionBackup:
    """Write the person's memories to their Notion page. Never raises; the row says how it went."""
    user_id = str(user_id)
    state = await get_state(user_id)
    stamp = saved.now_str()
    try:
        notion = await _open(user_id)
        data = await snapshot(user_id)
        content = render(data)
        page_id, page_url = state.page_id, state.page_url
        written = False
        if page_id and not state.owned and trigger != 'manual':
            if not state.restored:
                page_id = ''  # not ours yet: check again whether it holds a bigger backup
            elif await _restored_waiting(user_id):
                return await _set(state, status=WAITING, last_at=stamp,
                                  message=('Some restored notes wait for your OK in Settings › Memory. The Notion '
                                           'backup stays as it is until you answer them, or press Back up now.'))
        if page_id:
            try:
                await notion.call('update-page', {'page_id': page_id, 'command': 'replace_content',
                                                  'new_str': content, 'allow_deleting_content': True})
                written = True
            except BackupError:
                # The page may have been deleted or moved out of reach: look for it, else start a new one.
                logger.info('Notion backup page %s could not be updated; looking for it again', page_id)
                page_id = ''
        if not written:
            found_id, found_url, found = await _find_page(notion, data.get('account') or '')
            theirs = len((found or {}).get('memories') or [])
            if found_id and trigger != 'manual' and theirs > len(data['memories']):
                # A fresh install tidying a few new notes must not wipe the backup it could restore.
                return await _set(state, page_id=found_id, page_url=found_url or state.page_url, status=RESTORE_FIRST,
                                  message=(f'Your Notion has a backup with {theirs} memories from '
                                           f"{(found or {}).get('saved') or 'another install'}. Restore it first, "
                                           'or press Back up now to replace it.'),
                                  last_at=stamp)
            if found_id:
                await notion.call('update-page', {'page_id': found_id, 'command': 'replace_content',
                                                  'new_str': content, 'allow_deleting_content': True})
                page_id, page_url = found_id, found_url or page_url
            else:
                raw = await notion.call('create-pages', {
                    'pages': [{'properties': {'title': PAGE_TITLE}, 'content': content, 'icon': '🧠'}]})
                page_id, page_url = _page_ref(raw)
                if not page_id:
                    raise BackupError('Notion made the page but did not say where. Try again later.')
        message = f"Saved {len(data['memories'])} memories to Notion."
        if data.get('left_out'):
            message += f" Left out {data['left_out']} that looked like a password or key."
        return await _set(state, page_id=page_id, page_url=page_url or state.page_url, status=OK, message=message,
                          last_at=stamp, last_ok=stamp, count=len(data['memories']), owned=True)
    except BackupError as exc:
        return await _set(state, status=exc.status, message=exc.message, last_at=stamp)
    except Exception:
        logger.exception('Notion memory backup failed for %s (%s)', user_id, trigger)
        return await _set(state, status=FAILED, message='The backup to Notion did not finish. Try again later.',
                          last_at=stamp)


async def _restored_waiting(user_id: str) -> int:
    """Brought-in notes (a restore or an import) still waiting for the person's OK."""
    return len([r for r in await saved.pending_memories(user_id) if r.source == 'import'])


async def after_tidy(user_id: str) -> None:
    """Called when a tidy finished. Quiet when backups are off; the status row covers failures.
    Skills are backed up too when they changed since their last backup."""
    try:
        state = await get_state(user_id)
        if not state.enabled:
            return
        await backup(user_id, trigger='tidy')
    except Exception:
        logger.exception('Notion memory backup after tidy failed for %s', user_id)
    from cognitrix.skills import notion_backup as skills_backup

    await skills_backup.after_change(user_id)


# ---------------------------------------------------------------- restore

def _unique_by_name(names: dict[str, str]) -> dict[str, str]:
    """Name -> id, for names only one agent or project has. Two with the same name
    are ambiguous, so their notes come back as proposals instead of to a guess."""
    seen: dict[str, list[str]] = {}
    for item_id, name in names.items():
        seen.setdefault(str(name or '').strip().lower(), []).append(item_id)
    return {name: ids[0] for name, ids in seen.items() if name and len(ids) == 1}


async def restore(user_id: str) -> dict[str, Any]:
    """Bring memories back from the person's Notion backup page. Raises BackupError."""
    user_id = str(user_id)
    state = await get_state(user_id)
    notion = await _open(user_id)
    data = None
    page_id, page_url = state.page_id, state.page_url
    if page_id:
        try:
            data = parse(await notion.call('fetch', {'id': page_id}))
        except BackupError:
            data = None
    if data is None:
        page_id, page_url, data = await _find_page(notion, await _account(user_id))
    if data is None:
        raise BackupError(f"There is no '{PAGE_TITLE}' page in your Notion yet. It is made after your first memory tidy.")

    agents, projects = await _names(user_id)
    agent_by_name = _unique_by_name(agents)
    project_by_name = _unique_by_name(projects)
    restored = already = waiting = left_out = 0
    for item in data.get('memories') or []:
        if not isinstance(item, dict) or not isinstance(item.get('content'), str):
            continue
        content = item['content']
        if saved.looks_secret(content):
            left_out += 1
            continue
        scope = item.get('scope') or 'global'
        kwargs: dict[str, Any] = {'scope': 'global', 'pending': False}
        if scope == 'agent':
            agent_id = agent_by_name.get(str(item.get('agent') or '').strip().lower())
            kwargs = {'scope': 'agent', 'agent_id': agent_id, 'pending': False} if agent_id else {'scope': 'global', 'pending': True}
        elif scope == 'project':
            project_id = project_by_name.get(str(item.get('project') or '').strip().lower())
            kwargs = ({'scope': 'project', 'project_id': project_id, 'pending': False} if project_id
                      else {'scope': 'global', 'pending': True})
        elif scope != 'global':
            kwargs = {'scope': 'global', 'pending': True}
        try:
            _, created = await saved.add(
                user_id, content, source='import', by='import', pinned=bool(item.get('pinned')),
                kind=item.get('kind') or '', valid_until=item.get('valid_until') or '', **kwargs)
        except saved.MemoryError_ as exc:
            if exc.code == 'memory_full':
                break
            left_out += 1
            continue
        if not created:
            already += 1
        elif kwargs.get('pending'):
            waiting += 1
        else:
            restored += 1

    about_restored = False
    about = str(data.get('about_you') or '').strip()
    if about and not saved.looks_secret(about):
        current = await saved.get_profile(user_id)
        if current is None or not current.text:
            try:
                await saved.set_profile(user_id, about[:saved.PROFILE_MAX_CHARS], locked=bool(data.get('about_you_locked')))
                about_restored = True
            except saved.MemoryError_:
                pass
    switched = bool(page_id) and page_id != state.page_id
    changes: dict[str, Any] = {'page_id': page_id or state.page_id, 'page_url': page_url or state.page_url,
                               'restored': True, 'last_at': saved.now_str()}
    if switched:
        changes['owned'] = False
    if waiting:
        changes.update(status=WAITING, message=(f'{waiting} restored notes wait for your OK in Settings › Memory. '
                                                'The Notion backup stays as it is until you answer them.'))
    else:
        changes.update(status=OK, message=f'Restored {restored} memories from Notion.')
    await _set(state, **changes)
    return {'restored': restored, 'already': already, 'waiting': waiting, 'left_out': left_out,
            'about_you': about_restored, 'saved': data.get('saved') or '', 'page_url': page_url or state.page_url}


async def offer_restore(user_id: str) -> bool:
    """After Notion is connected on an install with no memories: if a backup exists, say so in the Inbox."""
    try:
        if [r for r in await saved.list_memories(user_id) if not r.pending]:
            return False
        notion = await _open(user_id)
        page_id, page_url, data = await _find_page(notion, await _account(user_id))
        if not page_id or data is None or not data.get('memories'):
            return False
        count = len(data['memories'])
        state = await get_state(user_id)
        await _set(state, page_id=page_id, page_url=page_url or state.page_url, status=RESTORE_FIRST, owned=False,
                   message=(f'Your Notion has a backup with {count} memories from '
                            f"{data.get('saved') or 'another install'}. Restore it first, or press Back up now to replace it."),
                   last_at=saved.now_str())
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            user_id, 'Memory backup found in Notion',
            f'Your Notion has a backup of {count} memories from {data.get("saved") or "an earlier install"}. '
            'Open Settings › Memory and press Restore from Notion to bring them back.',
            link='/settings/memory')
        return True
    except BackupError:
        return False
    except Exception:
        logger.debug('Could not look for a Notion memory backup', exc_info=True)
        return False


def public_state(row: NotionBackup, connected: bool) -> dict[str, Any]:
    return {'enabled': bool(row.enabled), 'connected': connected, 'status': row.status or '',
            'message': row.message or '', 'last_at': row.last_at or None, 'last_ok': row.last_ok or None,
            'page_url': row.page_url or None, 'count': row.count}
