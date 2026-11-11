"""Start a task when new mail arrives in a connected Gmail account.

A new-mail trigger is a ``TaskTrigger`` of kind "gmail" that points at its
owner's Gmail connection (Settings > Connected apps). Nothing calls us when
mail arrives, so the scheduler asks Gmail every couple of minutes what
changed since the last look (Gmail's history list, which is cheap) and stores
new inbox messages as one ``TriggerEvent``. From there the event is handled
exactly like a webhook: one run of the task, with the event as untrusted data.

Only the sender, subject, date and Gmail's own short snippet of each message
go into the event. The agent reads the rest with its Gmail tools, under the
usual approval rules.

Checks use Gmail's REST API with the connection's sign-in, so the Gmail API
has to be on in the Google Cloud project (the setup guide turns it on).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import httpx

from cognitrix.tasks.automation import (
    EVENT_MAX_BYTES,
    FMT,
    MAIL_KIND,
    MAX_PENDING_EVENTS,
    PENDING,
    TaskTrigger,
    TriggerEvent,
    utcnow,
)

logger = logging.getLogger('cognitrix.log')

GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me'
GMAIL_APP = 'gmail'
HTTP_TIMEOUT = 20
MAX_MESSAGES = 10          # per event; more are counted, not listed
MAX_HISTORY_PAGES = 3
MIN_SECONDS, DEFAULT_SECONDS = 30, 120


class MailCheckError(Exception):
    """A check failed; the message is shown to the trigger's owner."""


def check_seconds() -> int:
    try:
        return max(MIN_SECONDS, int(os.getenv('COGNITRIX_MAIL_CHECK_SECONDS', '') or DEFAULT_SECONDS))
    except ValueError:
        return DEFAULT_SECONDS


async def gmail_connection(owner: str | None, source_id: str | None = None) -> Any | None:
    """The owner's signed-in Gmail connection (the one named, when given)."""
    from cognitrix.models.mcp_server import MCPServer

    if not owner:
        return None
    for row in await MCPServer.find({'owner_id': str(owner)}) or []:
        if (row.catalog_id or '') != GMAIL_APP or (row.auth_type or '') != 'oauth':
            continue
        if source_id and str(row.id) != str(source_id):
            continue
        return row
    return None


async def _get(client: httpx.AsyncClient, path: str, params: Any) -> tuple[int, Any]:
    try:
        response = await client.get(f'{GMAIL_API}/{path}', params=params)
    except httpx.HTTPError:
        raise MailCheckError("Couldn't reach Gmail.")
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    return response.status_code, data


def _refused(status: int, data: Any) -> MailCheckError:
    message = ''
    if isinstance(data, dict) and isinstance(data.get('error'), dict):
        message = str(data['error'].get('message') or '')
    if status == 403 and 'has not been used' in message:
        return MailCheckError('The Gmail API is turned off in the Google Cloud project. An admin can turn it on from Settings › Connected apps › Set up.')
    return MailCheckError(f'Gmail refused the check ({status}).')


def _header(message: dict[str, Any], name: str) -> str:
    for header in (message.get('payload') or {}).get('headers') or []:
        if str(header.get('name') or '').lower() == name:
            return str(header.get('value') or '')[:300]
    return ''


def _matches(mail: dict[str, str], match: str) -> bool:
    match = match.strip().lower()
    return not match or match in mail['from'].lower() or match in mail['subject'].lower()


async def new_mail(token: str, cursor: str | None, match: str = '') -> tuple[str, list[dict[str, str]], int]:
    """What arrived in the inbox since ``cursor``.

    Returns (new cursor, up to MAX_MESSAGES messages, how many more there were).
    With no cursor, or one Gmail no longer knows, only the starting point is
    returned: mail that was already there never starts a run.
    """
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
        async def baseline() -> str:
            status, data = await _get(client, 'profile', {'fields': 'historyId'})
            if status != 200 or not data.get('historyId'):
                raise _refused(status, data)
            return str(data['historyId'])

        if not cursor:
            return await baseline(), [], 0

        ids: list[str] = []
        latest = cursor
        page: str | None = None
        for _ in range(MAX_HISTORY_PAGES):
            params: dict[str, Any] = {
                'startHistoryId': cursor, 'historyTypes': 'messageAdded', 'labelId': 'INBOX', 'maxResults': 100,
            }
            if page:
                params['pageToken'] = page
            status, data = await _get(client, 'history', params)
            if status == 404:
                return await baseline(), [], 0  # too old: start again from now
            if status != 200:
                raise _refused(status, data)
            latest = str(data.get('historyId') or latest)
            for record in data.get('history') or []:
                for added in record.get('messagesAdded') or []:
                    message = added.get('message') or {}
                    labels = set(message.get('labelIds') or [])
                    if 'INBOX' in labels and not labels & {'DRAFT', 'SENT', 'SPAM', 'TRASH'}:
                        if message.get('id') and message['id'] not in ids:
                            ids.append(str(message['id']))
            page = data.get('nextPageToken')
            if not page:
                break

        mails: list[dict[str, str]] = []
        skipped = 0
        for message_id in ids:
            if len(mails) >= MAX_MESSAGES:
                skipped += 1
                continue
            status, data = await _get(client, f'messages/{message_id}', [
                ('format', 'metadata'), ('metadataHeaders', 'From'), ('metadataHeaders', 'Subject'),
                ('metadataHeaders', 'Date'), ('metadataHeaders', 'Message-ID'),
            ])
            if status == 404:
                continue  # deleted or moved since
            if status != 200:
                raise _refused(status, data)
            mail = {
                'id': str(data.get('id') or message_id),
                'thread_id': str(data.get('threadId') or ''),
                'from': _header(data, 'from'),
                'subject': _header(data, 'subject'),
                'date': _header(data, 'date'),
                'message_id': _header(data, 'message-id'),
                'snippet': str(data.get('snippet') or '')[:300],
            }
            if _matches(mail, match):
                mails.append(mail)
        return latest, mails, skipped


def _payload(account: str, mails: list[dict[str, str]], more: int) -> str:
    body: dict[str, Any] = {'account': account, 'new_messages': mails}
    if more:
        body['more_not_listed'] = more
    text = json.dumps(body, ensure_ascii=False)
    while len(text.encode('utf-8')) > EVENT_MAX_BYTES and body['new_messages']:
        body['new_messages'] = body['new_messages'][:-1]
        body['more_not_listed'] = body.get('more_not_listed', 0) + 1
        text = json.dumps(body, ensure_ascii=False)
    return text


async def _check(trigger: TaskTrigger, now: datetime) -> bool:
    """Check one trigger. True when it queued an event."""
    from cognitrix.mcp import oauth

    row = await gmail_connection(trigger.owner_id, trigger.source_id)
    if row is None:
        raise MailCheckError('Gmail is no longer connected. Connect it in Settings › Connected apps.')
    try:
        token = await oauth.access_token(row)
    except oauth.ReconnectNeeded:
        raise MailCheckError('Gmail needs reconnecting in Settings › Connected apps.')
    except httpx.HTTPError:
        raise MailCheckError("Couldn't reach Google to renew the Gmail sign-in.")

    cursor, mails, more = await new_mail(token, trigger.cursor, trigger.match or '')
    queued = False
    if mails:
        waiting = await TriggerEvent.find({'task_id': trigger.task_id, 'status': PENDING}) or []
        if len(waiting) >= MAX_PENDING_EVENTS:
            # Leave the cursor where it is, so this mail is seen again next time.
            raise MailCheckError('Too many events are waiting for this task; new mail will be picked up once they have run.')
        await TriggerEvent(
            trigger_id=str(trigger.id), task_id=str(trigger.task_id),
            payload=_payload(str(row.account or ''), mails, more),
            content_type='application/json', received_at=now.strftime(FMT),
        ).save()
        queued = True
    await TaskTrigger.update_one({'id': trigger.id}, {'cursor': cursor, 'error': None})
    return queued


async def poll_mail_triggers(now: datetime | None = None) -> int:
    """Check every new-mail trigger that is due. Returns how many queued an event."""
    now = now or utcnow()
    due_before = (now - timedelta(seconds=check_seconds())).strftime(FMT)
    stamp = now.strftime(FMT)
    queued = 0
    try:
        triggers = await TaskTrigger.find({'kind': MAIL_KIND}) or []
    except Exception as exc:
        if 'no such table' in str(exc).lower():
            return 0
        raise
    for trigger in triggers:
        if not trigger.enabled or (trigger.checked_at and trigger.checked_at > due_before):
            continue
        # Claim the check, so two processes never read the same mail twice.
        # (A trigger that was never checked has no value to compare against.)
        claim: dict[str, Any] = {'id': trigger.id}
        if trigger.checked_at is not None:
            claim['checked_at'] = trigger.checked_at
        if await TaskTrigger.update_one(claim, {'checked_at': stamp}) != 1:
            continue
        try:
            queued += 1 if await _check(trigger, now) else 0
        except MailCheckError as exc:
            if str(exc) != (trigger.error or ''):
                await TaskTrigger.update_one({'id': trigger.id}, {'error': str(exc)[:300]})
        except Exception:
            logger.exception('New-mail check failed for trigger %s', trigger.id)
    return queued
