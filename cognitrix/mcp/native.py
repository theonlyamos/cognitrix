"""Built-in tools that sit next to a connected app's own (MCP) tools.

Google's Gmail MCP server can read, label and draft, but not send mail. When
a person turns on "Send email" for their Gmail connection
(``catalog.AppOption``), the tools here are added to that connection. They
call Gmail's REST API with the connection's own sign-in, so nothing new is
stored. (Calendar changes used to live here too; Google's Calendar server now
has its own create, update, delete and respond tools, see ``AppOption.tools``.)

GitHub connections always have Watch Pull Request and its companions
(``APP_TOOLS``): they wake the agent when a pull request changes
(``cognitrix/tasks/pr_watch.py``).

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

import httpx

from cognitrix.mcp import catalog

logger = logging.getLogger('cognitrix.log')

HTTP_TIMEOUT = 30
GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me'

Handler = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class NativeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    # Sends and changes ask first: nothing here is read-only.
    annotations: dict[str, Any]
    # The handler also takes the connection row: handler(token, args, row).
    with_row: bool = False

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


# --- GitHub -------------------------------------------------------------------

async def _watch_pr(token: str, args: dict[str, Any], row: Any) -> str:
    from cognitrix.tasks import pr_watch

    try:
        return await pr_watch.start_watch(row, token, str(args.get('pull_request') or ''),
                                          str(args.get('instructions') or ''))
    except pr_watch.WatchError as exc:
        raise NativeToolError(str(exc))


async def _stop_watching_pr(token: str, args: dict[str, Any], row: Any) -> str:
    from cognitrix.tasks import pr_watch

    try:
        return await pr_watch.stop_matching(str(args.get('pull_request') or ''))
    except pr_watch.WatchError as exc:
        raise NativeToolError(str(exc))


async def _list_watched_prs(token: str, args: dict[str, Any], row: Any) -> str:
    from cognitrix.tasks import pr_watch

    try:
        return await pr_watch.list_for_context()
    except pr_watch.WatchError as exc:
        raise NativeToolError(str(exc))


_PR = {'pull_request': {'type': 'string', 'description': 'The pull request: owner/repo#123 or its github.com link.'}}

# Watching only reads GitHub (and, on a server GitHub can reach, adds a
# webhook to the repository), so it doesn't ask first.
GITHUB_WATCH = (
    NativeTool(
        name='watch_pull_request',
        description=(
            'Get woken up when something happens on a GitHub pull request: a new review or comment, a CI '
            'check that fails, all checks passing, ready for review, merged or closed. In a chat the news '
            'arrives in this chat as a new message; in a task run it starts a new run of the task. Use it '
            'after opening a pull request or when asked to keep an eye on one, instead of checking over '
            'and over. Watching stops on its own when the pull request is merged or closed.'),
        input_schema=_schema({
            **_PR,
            'instructions': {'type': 'string', 'description': (
                'What you mean to do when news arrives, e.g. "fix failing checks and answer review '
                'comments". It is shown to you with each update.')},
        }, ['pull_request']),
        handler=_watch_pr,
        annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False,
                     'title': 'Watch pull request'},
        with_row=True,
    ),
    NativeTool(
        name='stop_watching_pull_request',
        description='Stop getting news about a GitHub pull request you watch from this chat or task.',
        input_schema=_schema(_PR, ['pull_request']),
        handler=_stop_watching_pr,
        annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False,
                     'title': 'Stop watching pull request'},
        with_row=True,
    ),
    NativeTool(
        name='list_watched_pull_requests',
        description='List the GitHub pull requests being watched for the person, and how.',
        input_schema=_schema({}, []),
        handler=_list_watched_prs,
        annotations={'readOnlyHint': True, 'title': 'List watched pull requests'},
        with_row=True,
    ),
)

# catalog app id -> tools every connection of that app has.
APP_TOOLS: dict[str, tuple[NativeTool, ...]] = {
    'github': GITHUB_WATCH,
}

# (catalog app id, option id) -> the tools that option adds.
OPTION_TOOLS: dict[tuple[str, str], tuple[NativeTool, ...]] = {
    ('gmail', 'send'): GMAIL_SEND,
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
    auth = getattr(row, 'auth_type', None) or ''
    if app is None or auth not in ('oauth', 'header'):
        return []
    tools: list[NativeTool] = list(APP_TOOLS.get(app.id, ()))
    if auth != 'oauth':
        return tools
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
        if (getattr(row, 'auth_type', None) or '') == 'header':
            from cognitrix.tasks.pr_watch import connection_token

            token = await connection_token(row)
        else:
            token = await oauth.access_token(row)
    except oauth.ReconnectNeeded as exc:
        return sign_in_message(oauth.app_name(row), exc.reason)
    except httpx.HTTPError:
        return f"Couldn't reach {oauth.app_name(row)} to renew its sign-in. Try again in a moment."
    try:
        clean = {k: v for k, v in (args or {}).items() if v is not None}
        if tool.with_row:
            return await tool.handler(token, clean, row)  # type: ignore[call-arg]
        return await tool.handler(token, clean)
    except NativeToolError as exc:
        return f'Error: {exc}'
