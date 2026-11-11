"""Built-in tools that sit next to a connected app's own (MCP) tools.

Google's Gmail and Calendar MCP servers can read and draft, but not send mail
or change events. When a person turns on "Send email" or "Create and change
events" for their connection (``catalog.AppOption``), the tools here are added
to that connection. They call Google's REST APIs with the connection's own
sign-in, so nothing new is stored.

To agents and to the permission settings they look like any other tool of
the app: they are listed, searched, allowed or switched off the same way
(``cognitrix/mcp/apps.py``). Every one of them asks before it runs unless the
person chose "always allow" for it.
"""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

import httpx

from cognitrix.mcp import catalog

logger = logging.getLogger('cognitrix.log')

HTTP_TIMEOUT = 30
GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me'
CALENDAR_API = 'https://www.googleapis.com/calendar/v3'

Handler = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class NativeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    # Sends and changes ask first: nothing here is read-only.
    annotations: dict[str, Any]

    def info(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self.input_schema,
            'annotations': dict(self.annotations),
        }


class NativeToolError(Exception):
    """A failure with a message written for the agent."""


def _schema(properties: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {'type': 'object', 'properties': properties, 'required': required}


async def _request(method: str, url: str, token: str, *, app: str, **kwargs: Any) -> Any:
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError:
        raise NativeToolError(f"Couldn't reach {app}. Try again in a moment.")
    if response.status_code == 204 or not response.content:
        data: Any = {}
    else:
        try:
            data = response.json()
        except ValueError:
            data = {}
    if response.status_code >= 400:
        detail = ''
        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            detail = str(data['error'].get('message') or '')
        if response.status_code == 403 and 'scope' in detail.lower():
            raise NativeToolError(
                f"{app} refused: this connection doesn't have permission for that yet. "
                'Tell the person to open Settings › Connected apps and reconnect it.'
            )
        raise NativeToolError(f'{app} refused the request ({response.status_code}). {detail}'.strip())
    return data


# --- Gmail --------------------------------------------------------------------

def _addresses(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v).strip() for v in value if str(v).strip())
    return str(value or '').strip()


def build_email(args: dict[str, Any]) -> str:
    """The message as base64url RFC 822 text, the form Gmail's API takes."""
    to = _addresses(args.get('to'))
    if not to:
        raise NativeToolError('Give at least one address in "to".')
    message = EmailMessage()
    message['To'] = to
    if _addresses(args.get('cc')):
        message['Cc'] = _addresses(args.get('cc'))
    if _addresses(args.get('bcc')):
        message['Bcc'] = _addresses(args.get('bcc'))
    message['Subject'] = str(args.get('subject') or '')
    reply_to = str(args.get('in_reply_to') or '').strip()
    if reply_to:
        message['In-Reply-To'] = reply_to
        message['References'] = reply_to
    message.set_content(str(args.get('body') or ''))
    if args.get('html_body'):
        message.add_alternative(str(args['html_body']), subtype='html')
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


async def _send_email(token: str, args: dict[str, Any]) -> str:
    body: dict[str, Any] = {'raw': build_email(args)}
    if args.get('thread_id'):
        body['threadId'] = str(args['thread_id'])
    sent = await _request('POST', f'{GMAIL_API}/messages/send', token, app='Gmail', json=body)
    return json.dumps({'sent': True, 'message_id': sent.get('id'), 'thread_id': sent.get('threadId')})


async def _send_draft(token: str, args: dict[str, Any]) -> str:
    draft_id = str(args.get('draft_id') or '').strip()
    if not draft_id:
        raise NativeToolError('Give the draft_id of the draft to send.')
    sent = await _request('POST', f'{GMAIL_API}/drafts/send', token, app='Gmail', json={'id': draft_id})
    return json.dumps({'sent': True, 'message_id': sent.get('id'), 'thread_id': sent.get('threadId')})


_ASKS = {'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': True}

GMAIL_SEND = (
    NativeTool(
        name='send_email',
        description=(
            'Send an email from the connected Gmail account. To answer a thread, pass its thread_id '
            'and the Message-ID you are replying to as in_reply_to, and keep the subject.'
        ),
        input_schema=_schema({
            'to': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Recipient addresses.'},
            'subject': {'type': 'string', 'description': 'Subject line.'},
            'body': {'type': 'string', 'description': 'Plain text of the message.'},
            'cc': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Cc addresses.'},
            'bcc': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Bcc addresses.'},
            'html_body': {'type': 'string', 'description': 'Optional HTML version of the message.'},
            'thread_id': {'type': 'string', 'description': 'Gmail thread to add the message to.'},
            'in_reply_to': {'type': 'string', 'description': 'Message-ID header of the mail this answers.'},
        }, ['to', 'subject', 'body']),
        handler=_send_email,
        annotations={**_ASKS, 'title': 'Send email'},
    ),
    NativeTool(
        name='send_draft',
        description='Send a draft that is already saved in the connected Gmail account, exactly as it is.',
        input_schema=_schema({
            'draft_id': {'type': 'string', 'description': 'The id of the saved draft.'},
        }, ['draft_id']),
        handler=_send_draft,
        annotations={**_ASKS, 'title': 'Send draft'},
    ),
)


# --- Google Calendar ------------------------------------------------------------

def _event_time(value: Any, time_zone: str | None) -> dict[str, str]:
    text = str(value or '').strip()
    if not text:
        raise NativeToolError('Give start and end as a date (2026-11-20) or a date and time (2026-11-20T15:00:00).')
    if 'T' not in text:
        return {'date': text}
    when: dict[str, str] = {'dateTime': text}
    if time_zone:
        when['timeZone'] = time_zone
    return when


def _event_body(args: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    body: dict[str, Any] = {}
    zone = str(args.get('time_zone') or '').strip() or None
    for field, key in (('title', 'summary'), ('description', 'description'), ('location', 'location')):
        if args.get(field) is not None:
            body[key] = str(args[field])
    for field in ('start', 'end'):
        if args.get(field) is not None:
            body[field] = _event_time(args[field], zone)
    if args.get('attendees') is not None:
        body['attendees'] = [{'email': str(a).strip()} for a in args['attendees'] if str(a).strip()]
    if not partial and not {'summary', 'start', 'end'} <= set(body):
        raise NativeToolError('An event needs a title, a start and an end.')
    return body


def _calendar_url(args: dict[str, Any], event: bool) -> str:
    calendar = quote(str(args.get('calendar_id') or 'primary'), safe='')
    url = f'{CALENDAR_API}/calendars/{calendar}/events'
    if event:
        event_id = str(args.get('event_id') or '').strip()
        if not event_id:
            raise NativeToolError('Give the event_id of the event.')
        url += f'/{quote(event_id, safe="")}'
    return url


def _notify(args: dict[str, Any]) -> dict[str, str]:
    return {'sendUpdates': 'all' if args.get('notify_attendees') else 'none'}


def _event_summary(event: dict[str, Any]) -> str:
    return json.dumps({
        'event_id': event.get('id'),
        'title': event.get('summary'),
        'start': event.get('start'),
        'end': event.get('end'),
        'link': event.get('htmlLink'),
    })


async def _create_event(token: str, args: dict[str, Any]) -> str:
    event = await _request('POST', _calendar_url(args, False), token, app='Google Calendar',
                           json=_event_body(args, partial=False), params=_notify(args))
    return _event_summary(event)


async def _update_event(token: str, args: dict[str, Any]) -> str:
    body = _event_body(args, partial=True)
    if not body:
        raise NativeToolError('Give at least one thing to change.')
    event = await _request('PATCH', _calendar_url(args, True), token, app='Google Calendar',
                           json=body, params=_notify(args))
    return _event_summary(event)


async def _delete_event(token: str, args: dict[str, Any]) -> str:
    await _request('DELETE', _calendar_url(args, True), token, app='Google Calendar', params=_notify(args))
    return json.dumps({'deleted': True, 'event_id': str(args.get('event_id'))})


_EVENT_FIELDS: dict[str, dict[str, Any]] = {
    'title': {'type': 'string', 'description': 'What the event is called.'},
    'start': {'type': 'string', 'description': 'Start: 2026-11-20T15:00:00, or 2026-11-20 for an all-day event.'},
    'end': {'type': 'string', 'description': 'End, in the same form as start.'},
    'time_zone': {'type': 'string', 'description': 'IANA time zone of start and end, e.g. Europe/Berlin.'},
    'description': {'type': 'string', 'description': 'Notes shown on the event.'},
    'location': {'type': 'string', 'description': 'Where it takes place.'},
    'attendees': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Email addresses to invite.'},
    'notify_attendees': {'type': 'boolean', 'description': 'Email the attendees about this. Off unless asked.'},
    'calendar_id': {'type': 'string', 'description': 'Which calendar. Leave out for the main one.'},
}
_EVENT_ID = {'event_id': {'type': 'string', 'description': 'The id of the event.'}}

CALENDAR_EDIT = (
    NativeTool(
        name='create_event',
        description='Add an event to the connected Google Calendar. Check free time first.',
        input_schema=_schema(_EVENT_FIELDS, ['title', 'start', 'end']),
        handler=_create_event,
        annotations={**_ASKS, 'title': 'Create event'},
    ),
    NativeTool(
        name='update_event',
        description='Change an event in the connected Google Calendar. Only the fields you give are changed.',
        input_schema=_schema({**_EVENT_ID, **_EVENT_FIELDS}, ['event_id']),
        handler=_update_event,
        annotations={**_ASKS, 'title': 'Change event'},
    ),
    NativeTool(
        name='delete_event',
        description='Delete an event from the connected Google Calendar.',
        input_schema=_schema({
            **_EVENT_ID,
            'notify_attendees': _EVENT_FIELDS['notify_attendees'],
            'calendar_id': _EVENT_FIELDS['calendar_id'],
        }, ['event_id']),
        handler=_delete_event,
        annotations={**_ASKS, 'title': 'Delete event'},
    ),
)

# (catalog app id, option id) -> the tools that option adds.
OPTION_TOOLS: dict[tuple[str, str], tuple[NativeTool, ...]] = {
    ('gmail', 'send'): GMAIL_SEND,
    ('google-calendar', 'edit'): CALENDAR_EDIT,
}


def option_granted(row: Any, chosen: catalog.AppOption) -> bool:
    """Whether the connection's sign-in covers the option's extra scopes."""
    if not chosen.scopes:
        return True
    from cognitrix.mcp import oauth

    return set(chosen.scopes) <= oauth.granted_scopes(row)


def tools_for(row: Any) -> list[NativeTool]:
    """The built-in tools this connection has right now."""
    app = catalog.get(getattr(row, 'catalog_id', None))
    if app is None or (getattr(row, 'auth_type', None) or '') != 'oauth':
        return []
    tools: list[NativeTool] = []
    for chosen in catalog.chosen_options(app, row):
        if option_granted(row, chosen):
            tools.extend(OPTION_TOOLS.get((app.id, chosen.id), ()))
    return tools


def find(row: Any, tool_name: str) -> NativeTool | None:
    return next((t for t in tools_for(row) if t.name == tool_name), None)


async def call(row: Any, tool: NativeTool, args: dict[str, Any]) -> str:
    """Run a built-in tool with the connection's sign-in."""
    from cognitrix.mcp import oauth
    from cognitrix.mcp.registry import sign_in_message

    try:
        token = await oauth.access_token(row)
    except oauth.ReconnectNeeded as exc:
        return sign_in_message(oauth.app_name(row), exc.reason)
    except httpx.HTTPError:
        return f"Couldn't reach {oauth.app_name(row)} to renew its sign-in. Try again in a moment."
    try:
        return await tool.handler(token, {k: v for k, v in (args or {}).items() if v is not None})
    except NativeToolError as exc:
        return f'Error: {exc}'
