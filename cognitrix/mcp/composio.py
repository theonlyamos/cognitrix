"""More connected apps through Composio, with the person's own API key.

Composio (composio.dev) is a service that signs in to hundreds of apps and
calls their APIs. It is optional and off until someone pastes their own
Composio API key in Settings > Connected apps. With a key:

- the page can search Composio's catalog and connect an app. Composio shows
  the sign-in and keeps the app's tokens; Cognitrix only keeps the id of the
  connected account;
- each connected app becomes an ``MCPServer`` row (``auth_type`` "composio",
  ``catalog_id`` "composio:<toolkit>") that agents pick like any other
  connection. Its tools are listed and called over Composio's REST API
  instead of MCP (``cognitrix/mcp/apps.py`` hides the difference), so they get
  the same search, approvals and per-tool Allow / Ask / Off settings.

The key itself is on one more row (``catalog_id`` "composio"), sealed, which
is never listed with the servers agents can use.

Everything a tool is sent and returns passes through Composio. That is the
trade-off of this provider, and the page says so before a key is saved.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from typing import Any

import httpx

logger = logging.getLogger('cognitrix.log')

ACCOUNT = 'composio'       # catalog_id of the row that holds the API key
PREFIX = 'composio:'       # catalog_id prefix of a connected Composio app
AUTH_TYPE = 'composio'
HTTP_TIMEOUT = 30
EXECUTE_TIMEOUT = 120
TOOLS_TTL = 600
MAX_TOOLS = 600
MAX_RESULT_CHARS = 60_000
SETTINGS_PAGE = '/settings/apps'

_SLUG = re.compile(r'^[a-z][a-z0-9_-]{0,79}$')
_TOOL = re.compile(r'^[A-Z][A-Z0-9_]{1,199}$')

# server id -> (fetched at, tools)
_tools_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class ComposioError(Exception):
    """A failure with a message written for the person (or the agent)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.user_message = message
        self.status = status


def base_url() -> str:
    return os.getenv('COMPOSIO_BASE_URL', '').strip().rstrip('/') or 'https://backend.composio.dev'


def is_app(config_or_row: Any) -> bool:
    """True for a connected Composio app (not the row that holds the key)."""
    return (getattr(config_or_row, 'auth_type', None) or '') == AUTH_TYPE and str(
        getattr(config_or_row, 'catalog_id', None) or '').startswith(PREFIX)


def is_account(row: Any) -> bool:
    return str(getattr(row, 'catalog_id', None) or '') == ACCOUNT


def toolkit_of(config_or_row: Any) -> str:
    return str(getattr(config_or_row, 'catalog_id', None) or '')[len(PREFIX):]


def composio_user(owner: str) -> str:
    """How this person is named inside the Composio project."""
    return f'cognitrix-{owner}'


# --- stored key and rows ----------------------------------------------------------

def _open(row: Any) -> dict[str, Any]:
    from cognitrix.common.secret_box import unseal_or_empty

    text = unseal_or_empty(getattr(row, 'oauth', None) or '')
    try:
        data = json.loads(text) if text else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _seal(data: dict[str, Any]) -> str:
    from cognitrix.common.secret_box import seal

    return seal(json.dumps(data))


async def _rows(owner: str) -> list[Any]:
    from cognitrix.models.mcp_server import MCPServer

    return [r for r in (await MCPServer.find({'owner_id': owner}) or [])]


async def account_row(owner: str) -> Any | None:
    return next((r for r in await _rows(owner) if is_account(r)), None)


async def app_rows(owner: str) -> list[Any]:
    return [r for r in await _rows(owner) if is_app(r)]


async def api_key(owner: str | None) -> str:
    if not owner:
        return ''
    row = await account_row(str(owner))
    return str(_open(row).get('api_key') or '') if row is not None else ''


async def key_status(owner: str) -> dict[str, Any]:
    row = await account_row(owner)
    key = str(_open(row).get('api_key') or '') if row is not None else ''
    return {'has_key': bool(key), 'hint': f'…{key[-4:]}' if key else None}


# --- HTTP ---------------------------------------------------------------------------

async def _call(key: str, method: str, path: str, *, timeout: float = HTTP_TIMEOUT, **kwargs: Any) -> Any:
    if not key:
        raise ComposioError('Add your Composio API key in Settings › Connected apps first.', 400)
    headers = {'x-api-key': key, 'Accept': 'application/json'}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, f'{base_url()}{path}', headers=headers, **kwargs)
    except httpx.HTTPError:
        raise ComposioError("Couldn't reach Composio. Try again in a moment.")
    if response.status_code in (401, 403):
        raise ComposioError("Composio didn't accept the API key. Check it in Settings › Connected apps.", 400)
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.status_code >= 400:
        detail = ''
        if isinstance(data, dict):
            error = data.get('error')
            detail = str((error.get('message') if isinstance(error, dict) else error) or data.get('message') or '')
        raise ComposioError(f'Composio refused the request ({response.status_code}). {detail[:300]}'.strip())
    return data


async def save_key(owner: str, key: str) -> dict[str, Any]:
    """Check the key with Composio, then keep it sealed on the person's key row."""
    from cognitrix.models.mcp_server import MCPServer

    key = key.strip()
    if not key or len(key) > 512 or any(c.isspace() for c in key):
        raise ComposioError('Paste the API key on its own.', 400)
    await _call(key, 'GET', '/api/v3.1/toolkits', params={'limit': 1})
    row = await account_row(owner)
    if row is None:
        row = MCPServer(
            name='composio_account', owner_id=owner, transport='http', url=base_url(),
            description='Composio API key', enabled=False, auth_type=AUTH_TYPE, catalog_id=ACCOUNT,
            oauth=_seal({'api_key': key}), status='connected', account='',
        )
        await row.save()
    else:
        await MCPServer.update_one({'id': row.id}, {'oauth': _seal({'api_key': key}), 'status': 'connected'})
    _tools_cache.clear()
    return await key_status(owner)


async def remove_key(owner: str) -> None:
    """Forget the key and every app that was connected with it."""
    from cognitrix.models.mcp_server import MCPServer

    for row in await _rows(owner):
        if is_account(row) or is_app(row):
            _tools_cache.pop(str(row.id), None)
            await MCPServer.delete_many({'id': row.id})


# --- catalog and connecting -----------------------------------------------------------

def _toolkit(item: dict[str, Any]) -> dict[str, Any] | None:
    slug = str(item.get('slug') or '').lower()
    if not _SLUG.match(slug):
        return None
    meta = item.get('meta') if isinstance(item.get('meta'), dict) else {}
    managed = item.get('composio_managed_auth_schemes') or []
    logo = str(meta.get('logo') or '')
    categories = [
        str(c.get('name')) for c in (meta.get('categories') or []) if isinstance(c, dict) and c.get('name')
    ]
    return {
        'slug': slug,
        'name': str(item.get('name') or slug)[:200],
        'description': str(meta.get('description') or '')[:400],
        'logo': logo if logo.startswith('https://') else '',
        'categories': categories[:4],
        'tools_count': int(meta.get('tools_count') or 0),
        # Composio can sign in to it without the person registering an OAuth app.
        'connectable': bool(managed) or bool(item.get('no_auth')),
    }


async def search_toolkits(owner: str, query: str = '', cursor: str | None = None) -> dict[str, Any]:
    key = await api_key(owner)
    params: dict[str, Any] = {'limit': 24, 'sort_by': 'usage'}
    if query.strip():
        params['search'] = query.strip()[:100]
    if cursor:
        params['cursor'] = cursor[:2048]
    data = await _call(key, 'GET', '/api/v3.1/toolkits', params=params)
    items = data.get('items') if isinstance(data, dict) else None
    toolkits = [t for t in (_toolkit(i) for i in (items or []) if isinstance(i, dict)) if t]
    return {'toolkits': toolkits, 'next_cursor': (data or {}).get('next_cursor') or None}


def _connection(row: Any) -> dict[str, Any]:
    return {
        'server_id': str(row.id),
        'slug': toolkit_of(row),
        'name': str(row.description or row.name),
        'status': row.status or 'signed_out',
        'account': row.account or '',
        'enabled': bool(row.enabled),
    }


async def connections(owner: str) -> list[dict[str, Any]]:
    return [_connection(r) for r in await app_rows(owner)]


def _unique_name(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name = f'{base}_{n}'
        n += 1
    return name


async def start_connect(owner: str, slug: str, name: str, public_base: str) -> dict[str, str]:
    """Begin connecting an app. Returns ``{'flow_id', 'authorize_url'}`` like other sign-ins."""
    from cognitrix.config import API_VERSION
    from cognitrix.mcp import oauth, registry
    from cognitrix.models.mcp_server import MCPServer

    slug = slug.strip().lower()
    if not _SLUG.match(slug):
        raise ComposioError('Unknown app.', 404)
    key = await api_key(owner)
    session = await _call(key, 'POST', '/api/v3.1/tool_router/session', json={
        'user_id': composio_user(owner),
        'toolkits': {'enable': [slug]},
        'manage_connections': {'enable': False},
    })
    session_id = str((session or {}).get('session_id') or '')
    if not session_id:
        raise ComposioError("Composio didn't start a sign-in. Try again in a moment.")
    flow_id = secrets.token_urlsafe(18)
    callback = f'{public_base.rstrip("/")}/api/{API_VERSION}/mcp/composio/callback/{flow_id}'
    link = await _call(key, 'POST', f'/api/v3.1/tool_router/session/{session_id}/link',
                       json={'toolkit': slug, 'callback_url': callback})
    redirect = str((link or {}).get('redirect_url') or '')
    account_id = str((link or {}).get('connected_account_id') or '')
    if not redirect.startswith('https://') or not account_id:
        raise ComposioError("Composio can't sign in to this app with its own setup. Set it up in your Composio project first.", 400)

    row = next((r for r in await app_rows(owner) if toolkit_of(r) == slug), None)
    label = (name or slug).strip()[:80]
    if row is None:
        taken = {c.name for c in await registry.visible_servers(owner)}
        row = MCPServer(
            name=_unique_name(re.sub(r'[^a-z0-9_]', '_', slug), taken), owner_id=owner, transport='http',
            url=base_url(), description=label, enabled=True, auth_type=AUTH_TYPE,
            catalog_id=f'{PREFIX}{slug}', oauth=_seal({'pending_account_id': account_id}),
            status='', account='',
        )
        await row.save()
        row = next((r for r in await app_rows(owner) if toolkit_of(r) == slug), row)
    else:
        data = _open(row)
        data['pending_account_id'] = account_id
        await MCPServer.update_one({'id': row.id}, {'oauth': _seal(data)})
    oauth.register_external_flow(flow_id, owner, str(row.id), label, check=_check_flow)
    return {'flow_id': flow_id, 'authorize_url': redirect}


async def _account_state(key: str, account_id: str) -> tuple[str, str]:
    """(status, label) of a Composio connected account."""
    data = await _call(key, 'GET', f'/api/v3.1/connected_accounts/{account_id}')
    status = str((data or {}).get('status') or '').upper()
    label = ''
    for source in ((data or {}).get('data'), (data or {}).get('params'), (data or {}).get('state')):
        if isinstance(source, dict):
            for field in ('email', 'user_email', 'login', 'username', 'account_name', 'workspace_name'):
                if isinstance(source.get(field), str) and source[field]:
                    label = source[field][:120]
                    break
        if label:
            break
    return status, label


async def _check_flow(owner: str, server_id: str) -> tuple[str, str | None]:
    """Ask Composio whether a pending sign-in finished. ('pending'|'done'|'error', message)."""
    from cognitrix.models.mcp_server import MCPServer

    row = await MCPServer.get(server_id)
    if row is None or str(row.owner_id or '') != owner or not is_app(row):
        return 'error', 'This app was removed while you were signing in.'
    data = _open(row)
    pending = str(data.get('pending_account_id') or '')
    if not pending:
        return ('done', None) if (row.status or '') == 'connected' else ('error', 'Sign-in was cancelled.')
    try:
        status, label = await _account_state(await api_key(owner), pending)
    except ComposioError as exc:
        return 'error', exc.user_message
    if status == 'ACTIVE':
        previous = str(data.get('account_id') or '')
        data = {'account_id': pending}
        await MCPServer.update_one({'id': row.id}, {
            'oauth': _seal(data), 'status': 'connected', 'account': label or (row.account or ''), 'enabled': True,
        })
        _tools_cache.pop(str(row.id), None)
        if previous and previous != pending:
            try:
                await _call(await api_key(owner), 'DELETE', f'/api/v3.1/connected_accounts/{previous}')
            except ComposioError:
                logger.debug('Could not remove the replaced Composio account')
        return 'done', None
    if status in ('FAILED', 'EXPIRED', 'REVOKED', 'INACTIVE'):
        return 'error', "The sign-in didn't finish. Please try again."
    return 'pending', None


async def disconnect(owner: str, server_id: str) -> None:
    """Remove the app here and its connected account at Composio."""
    from cognitrix.models.mcp_server import MCPServer

    row = await MCPServer.get(server_id)
    if row is None or str(row.owner_id or '') != owner or not is_app(row):
        raise ComposioError('App not found.', 404)
    data = _open(row)
    key = await api_key(owner)
    for field in ('account_id', 'pending_account_id'):
        account_id = str(data.get(field) or '')
        if account_id and key:
            try:
                await _call(key, 'DELETE', f'/api/v3.1/connected_accounts/{account_id}')
            except ComposioError:
                logger.debug('Could not remove a Composio account while disconnecting')
    _tools_cache.pop(str(row.id), None)
    await MCPServer.delete_many({'id': row.id})


# --- tools ------------------------------------------------------------------------------

def _annotations(tags: Any) -> dict[str, Any]:
    tags = {str(t) for t in tags} if isinstance(tags, list) else set()
    notes: dict[str, Any] = {}
    if 'readOnlyHint' in tags:
        notes['readOnlyHint'] = True
    elif 'destructiveHint' in tags:
        notes['destructiveHint'] = True
    return notes


def _tool_info(item: dict[str, Any], slug: str) -> dict[str, Any] | None:
    name = str(item.get('slug') or '')
    toolkit = item.get('toolkit') if isinstance(item.get('toolkit'), dict) else {}
    if not _TOOL.match(name) or str(toolkit.get('slug') or '').lower() != slug or item.get('is_deprecated'):
        return None
    schema = item.get('input_parameters')
    return {
        'name': name,
        'description': str(item.get('description') or item.get('human_description') or '')[:2000],
        'input_schema': schema if isinstance(schema, dict) else {'type': 'object', 'properties': {}},
        'annotations': {**_annotations(item.get('tags')), 'title': str(item.get('name') or '')[:120]},
    }


async def prepare(config: Any) -> str | None:
    """None when the app can be used, else why not (for the agent)."""
    from cognitrix.mcp.registry import sign_in_message

    name = str(config.description or config.name)
    if not await api_key(config.owner_id):
        return sign_in_message(name, 'setup')
    if (config.status or '') != 'connected':
        return sign_in_message(name, 'never')
    return None


async def list_tools(row: Any) -> list[dict[str, Any]]:
    """The app's tools in the shape MCP tools have (name, description, input_schema, annotations)."""
    key_id = str(row.id)
    cached = _tools_cache.get(key_id)
    if cached and time.time() - cached[0] < TOOLS_TTL:
        return cached[1]
    slug = toolkit_of(row)
    key = await api_key(str(row.owner_id or ''))
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(tools) < MAX_TOOLS:
        params: dict[str, Any] = {'toolkit_slug': slug, 'limit': 200}
        if cursor:
            params['cursor'] = cursor
        data = await _call(key, 'GET', '/api/v3.1/tools', params=params)
        items = data.get('items') if isinstance(data, dict) else None
        tools.extend(t for t in (_tool_info(i, slug) for i in (items or []) if isinstance(i, dict)) if t)
        cursor = (data or {}).get('next_cursor') or None
        if not cursor or not items:
            break
    _tools_cache[key_id] = (time.time(), tools)
    return tools


async def call_tool(row: Any, tool_name: str, args: dict[str, Any]) -> str:
    """Run one of the app's tools with the person's connected account."""
    from cognitrix.mcp.registry import sign_in_message

    name = str(row.description or row.name)
    try:
        known = {t['name'] for t in await list_tools(row)}
        if tool_name not in known:
            return f"Error: {name} has no tool named '{tool_name}'. Look the name up first."
        account_id = str(_open(row).get('account_id') or '')
        if not account_id:
            return sign_in_message(name, 'never')
        owner = str(row.owner_id or '')
        result = await _call(
            await api_key(owner), 'POST', f'/api/v3.1/tools/execute/{tool_name}', timeout=EXECUTE_TIMEOUT,
            json={'user_id': composio_user(owner), 'connected_account_id': account_id, 'arguments': args or {}},
        )
    except ComposioError as exc:
        return f'Error: {exc.user_message}'
    if not isinstance(result, dict):
        return 'Error: Composio returned nothing for this action.'
    if result.get('successful') is False or result.get('error'):
        detail = str(result.get('error') or 'the app refused the action')[:2000]
        if re.search(r'connected account|not connected|expired|revoked|unauthori[sz]ed|invalid_grant', detail, re.I):
            return f'{sign_in_message(name)} ({detail[:300]})'
        return f'Error: {name} could not do this: {detail}'
    text = json.dumps(result.get('data') if result.get('data') is not None else {}, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '\n[Result cut off. Ask for less at a time: narrow the query or lower the limit.]'
    return text
