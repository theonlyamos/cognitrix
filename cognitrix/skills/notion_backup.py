"""Keep a copy of a person's own skills in their Notion, and bring them back.

The chat review writes skills from finished work (and people write or teach
their own); they live only in this install's database. Like the memory backup
(``cognitrix.memory.notion_backup``), they are copied to one page in the
person's Notion, "Cognitrix skills backup", through their Notion connected app:
after a chat review or a tidy that left them changed, and with Back up now. The
page is replaced each time, never duplicated, and found again by its title on
a fresh install.

Restoring adds the skills this install doesn't have. A skill with the same
name that is already here is kept as it is (it is the newer one), and so is a
shared skill with that name. The same switch as the memory backup turns
automatic backups on and off.

Nothing here raises to the caller except ``restore``: the status row says how
the last backup went. Skills that look like they hold a password, key or token
are never written out.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from odbms import Model
from pydantic import field_validator

from cognitrix.memory import notion_backup as memory_backup
from cognitrix.memory import saved
from cognitrix.memory.notion_backup import (
    _CODE,
    FAILED,
    OK,
    RESTORE_FIRST,
    BackupError,
    _account,
    _page_ref,
    _page_text,
    _search_hits,
)

logger = logging.getLogger('cognitrix.log')

PAGE_TITLE = 'Cognitrix skills backup'
FORMAT = 'cognitrix-skills-backup'


class NotionSkillsBackup(Model):
    """One row per person: where their skills page is and how the last backup went."""
    user_id: str
    page_id: str = ''
    page_url: str = ''
    status: str = ''
    """'' (never ran), 'ok', 'failed', 'not_connected' or 'restore_first'."""
    message: str = ''
    last_at: str = ''
    last_ok: str = ''
    count: int = 0
    digest: str = ''
    """Fingerprint of what the last backup wrote, so unchanged skills aren't written again."""
    owned: bool = False
    """This install wrote ``page_id`` (or restored from it), so a backup may replace it."""

    @field_validator('page_id', 'page_url', 'status', 'message', 'last_at', 'last_ok', 'digest', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('owned', mode='before')
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


MODELS = (NotionSkillsBackup,)


async def get_state(user_id: str) -> NotionSkillsBackup:
    try:
        row = await NotionSkillsBackup.find_one({'user_id': str(user_id)})
    except Exception as exc:
        if 'no such table' not in str(exc).lower():
            raise
        row = None
    if row is None:
        row = NotionSkillsBackup(user_id=str(user_id))
        await row.save()
        row = await NotionSkillsBackup.find_one({'user_id': str(user_id)}) or row
    return row


async def _set(row: NotionSkillsBackup, **changes: Any) -> NotionSkillsBackup:
    await NotionSkillsBackup.update_one({'id': row.id}, changes)
    for key, value in changes.items():
        setattr(row, key, value)
    return row


# ---------------------------------------------------------------- what goes in the page

async def snapshot(user_id: str) -> dict[str, Any]:
    """The person's own skills. Ones that look like they hold a secret are left out."""
    from cognitrix.skills import personal

    items = []
    skipped = 0
    for row in await personal.skills_of(user_id):
        if saved.looks_secret(f'{row.description}\n{row.body}'):
            skipped += 1
            continue
        # The body as a list of lines keeps every JSON line short in Notion, and a line of the
        # body that opens a code fence can't end the page's data block early.
        items.append({'name': row.name, 'description': row.description, 'author': row.author or '',
                      'body': str(row.body).split('\n')})
    return {'format': FORMAT, 'version': 1, 'account': await _account(user_id), 'saved': saved.now_str(),
            'skills': items, 'left_out': skipped}


def digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data.get('skills') or [], sort_keys=True).encode()).hexdigest()[:32]


def _body(item: dict[str, Any]) -> str:
    body = item.get('body')
    return '\n'.join(str(line) for line in body) if isinstance(body, list) else str(body or '')


def render(data: dict[str, Any]) -> str:
    """A readable list on top (names and when each is used), the copy the app restores from below."""
    items = data.get('skills') or []
    lines = [
        f"Skills your Cognitrix agents use, saved {data.get('saved', '')} UTC. Most are learned from finished chats "
        'by the chat review. This page is replaced when they change. To bring them back after reinstalling, '
        'connect Notion and press Restore from Notion in Settings › Memory.',
        '',
    ]
    if not items:
        lines += ['No skills yet.', '']
    else:
        lines += ['## Skills', '']
        for item in items:
            by = f" ({item['author']})" if item.get('author') else ''
            lines.append(f"- **{item['name']}**{by}: {' '.join(str(item.get('description') or '').split())}")
        lines.append('')
    lines += ['## Backup data', '', 'Cognitrix restores from this block. Please leave it as it is.', '',
              '```json', json.dumps(data, ensure_ascii=False, indent=1), '```']
    return '\n'.join(lines)


def parse(raw: str) -> dict[str, Any] | None:
    """Read a skills backup page back. None when it holds none (only the data block has the instructions)."""
    text = _page_text(raw)
    for block in _CODE.findall(text):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('format') == FORMAT and isinstance(data.get('skills'), list):
            return data
    return None


async def _find_page(notion: Any, account: str) -> tuple[str, str, dict[str, Any] | None]:
    raw = await notion.call('search', {'query': PAGE_TITLE})
    fallback: tuple[str, str, dict[str, Any] | None] = ('', '', None)
    for page_id, url in _search_hits(raw, PAGE_TITLE)[:5]:
        try:
            data = parse(await notion.call('fetch', {'id': page_id}))
        except BackupError:
            continue
        if data is None:
            continue
        if account and data.get('account') == account:
            return page_id, url, data
        if not fallback[0]:
            fallback = (page_id, url, data)
    return fallback


# ---------------------------------------------------------------- back up

async def backup(user_id: str, *, trigger: str = 'review') -> NotionSkillsBackup:
    """Write the person's skills to their Notion page. Never raises; the row says how it went."""
    user_id = str(user_id)
    state = await get_state(user_id)
    stamp = saved.now_str()
    try:
        data = await snapshot(user_id)
        fingerprint = digest(data)
        if not data['skills'] and not state.page_id:
            return state  # no skills yet, and no page to empty: nothing to write
        if trigger != 'manual' and state.status == OK and state.owned and state.digest == fingerprint:
            return state  # nothing changed since the last backup
        notion = await memory_backup._open(user_id)
        content = render(data)
        page_id, page_url = (state.page_id, state.page_url) if state.owned or trigger == 'manual' else ('', '')
        written = False
        if page_id:
            try:
                await notion.call('update-page', {'page_id': page_id, 'command': 'replace_content',
                                                  'new_str': content, 'allow_deleting_content': True})
                written = True
            except BackupError:
                logger.info('Notion skills page %s could not be updated; looking for it again', page_id)
                page_id = ''
        if not written:
            found_id, found_url, found = await _find_page(notion, data.get('account') or '')
            ours = {item['name'] for item in data['skills']}
            missing = [i for i in (found or {}).get('skills') or [] if isinstance(i, dict) and i.get('name') not in ours]
            theirs = len((found or {}).get('skills') or [])
            if found_id and trigger != 'manual' and missing:
                # A fresh install must not wipe skills of a backup it could still restore.
                return await _set(state, page_id=found_id, page_url=found_url or state.page_url, status=RESTORE_FIRST,
                                  owned=False, last_at=stamp,
                                  message=(f'Your Notion has a backup with {theirs} skills from '
                                           f"{(found or {}).get('saved') or 'another install'}. Restore it first, "
                                           'or press Back up now to replace it.'))
            if found_id:
                await notion.call('update-page', {'page_id': found_id, 'command': 'replace_content',
                                                  'new_str': content, 'allow_deleting_content': True})
                page_id, page_url = found_id, found_url or page_url
            else:
                raw = await notion.call('create-pages', {
                    'pages': [{'properties': {'title': PAGE_TITLE}, 'content': content, 'icon': '🛠️'}]})
                page_id, page_url = _page_ref(raw)
                if not page_id:
                    raise BackupError('Notion made the page but did not say where. Try again later.')
        message = f"Saved {len(data['skills'])} skills to Notion."
        if data.get('left_out'):
            message += f" Left out {data['left_out']} that looked like they hold a password or key."
        return await _set(state, page_id=page_id, page_url=page_url or state.page_url, status=OK, message=message,
                          last_at=stamp, last_ok=stamp, count=len(data['skills']), digest=fingerprint, owned=True)
    except BackupError as exc:
        return await _set(state, status=exc.status, message=exc.message, last_at=stamp)
    except Exception:
        logger.exception('Notion skills backup failed for %s (%s)', user_id, trigger)
        return await _set(state, status=FAILED, message='The skills backup to Notion did not finish. Try again later.',
                          last_at=stamp)


async def after_change(user_id: str, trigger: str = 'review') -> None:
    """After a chat review or a tidy: back up when backups are on and the skills changed. Never raises."""
    try:
        if not (await memory_backup.get_state(user_id)).enabled:
            return
        if await memory_backup.find_connection(user_id) is None:
            return  # nothing to report: Settings shows "Connect Notion" already
        await backup(user_id, trigger=trigger)
    except Exception:
        logger.exception('Notion skills backup after %s failed for %s', trigger, user_id)


# ---------------------------------------------------------------- restore

async def restore(user_id: str) -> dict[str, Any]:
    """Add the skills from the Notion backup this install doesn't have. Raises BackupError."""
    from cognitrix.skills import personal
    from cognitrix.skills.drafts import DraftError

    user_id = str(user_id)
    state = await get_state(user_id)
    notion = await memory_backup._open(user_id)
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
        raise BackupError(f"There is no '{PAGE_TITLE}' page in your Notion yet.")

    mine = {row.name: row for row in await personal.skills_of(user_id)}
    restored = already = kept = left_out = not_fitted = 0
    items = data.get('skills') or []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        description = str(item.get('description') or '')
        body = _body(item)
        if not name or saved.looks_secret(f'{description}\n{body}'):
            left_out += 1
            continue
        here = mine.get(name)
        if here is not None:
            if here.body.strip() == body.strip():
                already += 1
            else:
                kept += 1  # this install's own copy is the newer one
            continue
        try:
            row = await personal.create(user_id, name, description, body, author=str(item.get('author') or ''))
        except DraftError as exc:
            if 'a lot of skills' in str(exc):
                not_fitted = len(items) - index
                break
            if 'already exists' in str(exc):
                kept += 1  # a shared skill has the name
            else:
                left_out += 1  # the text no longer passes the checks
            continue
        mine[row.name] = row
        restored += 1
    if not_fitted:
        # Some skills are still only in Notion: keep the page safe from automatic backups.
        await _set(state, page_id=page_id or state.page_id, page_url=page_url or state.page_url, owned=False,
                   status=RESTORE_FIRST, last_at=saved.now_str(),
                   message=(f"Restored {restored} skills, but {not_fitted} didn't fit (you can keep "
                            f'{personal.MAX_PER_PERSON}). Delete some you no longer use and restore again. '
                            'Until then the Notion page is kept as it is.'))
    else:
        await _set(state, page_id=page_id or state.page_id, page_url=page_url or state.page_url, owned=True,
                   status=OK, last_at=saved.now_str(), message=f'Restored {restored} skills from Notion.')
    return {'restored': restored, 'already': already, 'kept': kept, 'left_out': left_out, 'not_fitted': not_fitted,
            'saved': data.get('saved') or '', 'page_url': page_url or state.page_url}


async def offer_restore(user_id: str) -> bool:
    """After Notion is connected on an install with no skills of its own: if a backup exists, say so."""
    from cognitrix.skills import personal

    try:
        if await personal.skills_of(user_id):
            return False
        notion = await memory_backup._open(user_id)
        page_id, page_url, data = await _find_page(notion, await _account(user_id))
        if not page_id or data is None or not data.get('skills'):
            return False
        count = len(data['skills'])
        state = await get_state(user_id)
        await _set(state, page_id=page_id, page_url=page_url or state.page_url, status=RESTORE_FIRST, owned=False,
                   message=(f'Your Notion has a backup with {count} skills from '
                            f"{data.get('saved') or 'another install'}. Restore it first, or press Back up now to replace it."),
                   last_at=saved.now_str())
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            user_id, 'Skills backup found in Notion',
            f'Your Notion has a backup of {count} skills from {data.get("saved") or "an earlier install"}. '
            'Open Settings › Memory and press Restore from Notion to bring them back.',
            link='/settings/memory')
        return True
    except BackupError:
        return False
    except Exception:
        logger.debug('Could not look for a Notion skills backup', exc_info=True)
        return False


def public_state(row: NotionSkillsBackup) -> dict[str, Any]:
    return {'status': row.status or '', 'message': row.message or '', 'last_at': row.last_at or None,
            'last_ok': row.last_ok or None, 'page_url': row.page_url or None, 'count': row.count}
