"""Signing in to connected apps (remote MCP servers that use OAuth).

The flow, for every app:

1. ``start_sign_in`` works out where to sign in, registers this engine as a
   client if needed, and returns an authorization address (OAuth 2.1 with
   PKCE). The UI opens it in a popup on the web, or in the system browser in
   the desktop app.
2. The app sends the browser back to a callback route with a code.
   ``finish_sign_in`` swaps the code for tokens and stores them, encrypted,
   on the person's ``MCPServer`` row.
3. The UI polls ``flow_status`` until the sign-in is done.

Where the sign-in happens:

- Catalog apps with ``auth == 'provider'`` (GitHub, Google) use the OAuth app
  this server already has for "Sign in with Google / GitHub", and the same
  callback address (``/api/v1/auth/oauth/<provider>/callback``), so nothing
  new has to be registered with the provider.
- Everything else follows the MCP authorization spec: protected-resource
  metadata, authorization-server metadata, then a client ID metadata document
  (when this server has a public https address) or dynamic client
  registration. Its callback is ``/api/v1/mcp/oauth/callback``.

Tokens are refreshed shortly before they expire (``access_token``). When a
refresh is refused, the row is marked ``needs_reauth`` and the owner gets an
Inbox alert with a Reconnect button; tools then answer with a message that
asks the person to reconnect instead of failing silently.

Pending sign-ins live in this process's memory for ten minutes, the same as
"Sign in with Google" (``api/routes/oauth.py``).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from cognitrix.mcp import catalog, netcheck

logger = logging.getLogger('cognitrix.log')

FLOW_TTL = 600
REFRESH_MARGIN = 120  # seconds before expiry that a token counts as stale
HTTP_TIMEOUT = 15
APPS_PAGE = '/settings/apps'

PROVIDER_ENDPOINTS = {
    'google': {
        'authorize': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token': 'https://oauth2.googleapis.com/token',
        'revoke': 'https://oauth2.googleapis.com/revoke',
    },
    'github': {
        'authorize': 'https://github.com/login/oauth/authorize',
        'token': 'https://github.com/login/oauth/access_token',
    },
}

MCP_PROTOCOL_VERSION = '2025-06-18'


class SignInError(Exception):
    """A sign-in problem with a message written for the person."""

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


class ReconnectNeeded(Exception):
    """The app has no usable sign-in; the person has to (re)connect it."""

    def __init__(self, app_name: str, reason: str = 'expired'):
        super().__init__(app_name)
        self.app_name = app_name
        self.reason = reason


@dataclass
class _Flow:
    state: str
    flow_id: str
    user_key: str
    server_id: str
    app_name: str
    verifier: str
    redirect_uri: str
    token_endpoint: str
    client_id: str
    client_secret: str | None
    auth_method: str
    resource: str | None
    provider: str | None
    allow_private: bool
    created: float


_flows: dict[str, _Flow] = {}             # state -> pending sign-in
_results: dict[str, dict[str, Any]] = {}  # flow id -> outcome
_refresh_locks: dict[str, asyncio.Lock] = {}


def _prune() -> None:
    cutoff = time.time() - FLOW_TTL
    for state in [s for s, f in _flows.items() if f.created < cutoff]:
        _flows.pop(state, None)
    for key in [k for k, v in _results.items() if v['created'] < cutoff]:
        _results.pop(key, None)


def owns_state(state: str | None) -> bool:
    """Whether a callback's ``state`` belongs to a connected-app sign-in."""
    return bool(state) and state in _flows


# --- stored sign-in -----------------------------------------------------------

def load(row: Any) -> dict[str, Any]:
    from cognitrix.common.secret_box import unseal_or_empty

    text = unseal_or_empty(getattr(row, 'oauth', None) or '')
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _seal(data: dict[str, Any]) -> str:
    from cognitrix.common.secret_box import seal

    return seal(json.dumps(data)) if data else ''


async def _save(server_id: str, data: dict[str, Any], **changes: Any) -> None:
    from cognitrix.models.mcp_server import MCPServer

    await MCPServer.update_one({'id': server_id}, {'oauth': _seal(data), **changes})


def app_name(row: Any) -> str:
    app = catalog.get(getattr(row, 'catalog_id', None))
    return app.name if app else str(getattr(row, 'name', '') or 'This app')


# --- discovery ----------------------------------------------------------------

async def _get_json(client: httpx.AsyncClient, url: str, *, allow_private: bool) -> dict[str, Any] | None:
    try:
        await netcheck.check_url(url, allow_private=allow_private)
        response = await client.get(url, headers={'Accept': 'application/json', 'MCP-Protocol-Version': MCP_PROTOCOL_VERSION})
    except (httpx.HTTPError, netcheck.UnsafeURL):
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _initialize_body() -> dict[str, Any]:
    return {
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {
            'protocolVersion': MCP_PROTOCOL_VERSION,
            'capabilities': {},
            'clientInfo': {'name': 'Cognitrix', 'version': '1'},
        },
    }


async def probe(url: str, token: str | None = None, *, allow_private: bool = False) -> httpx.Response | None:
    """Send an MCP ``initialize`` and return the raw response (None if unreachable)."""
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        await netcheck.check_url(url, allow_private=allow_private)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            return await client.post(url, json=_initialize_body(), headers=headers)
    except (httpx.HTTPError, netcheck.UnsafeURL):
        return None


async def needs_sign_in(url: str, *, allow_private: bool = False) -> bool:
    """True when the server answers a plain ``initialize`` with 401."""
    response = await probe(url, allow_private=allow_private)
    return response is not None and response.status_code == 401


def _www_auth_field(response: httpx.Response | None, name: str) -> str | None:
    import re

    if response is None:
        return None
    header = response.headers.get('WWW-Authenticate') or ''
    match = re.search(rf'{name}=(?:"([^"]+)"|([^\s,]+))', header)
    return (match.group(1) or match.group(2)) if match else None


async def discover(url: str, *, allow_private: bool) -> dict[str, Any]:
    """Find a server's authorization server and its endpoints."""
    from mcp.client.auth.utils import (
        build_oauth_authorization_server_metadata_discovery_urls,
        build_protected_resource_metadata_discovery_urls,
    )

    challenge = await probe(url, allow_private=allow_private)
    if challenge is None:
        raise SignInError("Couldn't reach the app. Check its address and try again.")
    prm_url = _www_auth_field(challenge, 'resource_metadata') if challenge.status_code in (401, 403) else None
    header_scope = _www_auth_field(challenge, 'scope')

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resource_meta: dict[str, Any] | None = None
        for candidate in build_protected_resource_metadata_discovery_urls(prm_url, url):
            resource_meta = await _get_json(client, candidate, allow_private=allow_private)
            if resource_meta:
                break
        servers = (resource_meta or {}).get('authorization_servers') or []
        auth_server = str(servers[0]) if servers else None

        metadata: dict[str, Any] | None = None
        for candidate in build_oauth_authorization_server_metadata_discovery_urls(auth_server, url):
            metadata = await _get_json(client, candidate, allow_private=allow_private)
            if metadata and metadata.get('authorization_endpoint') and metadata.get('token_endpoint'):
                break
            metadata = None

    if not metadata:
        raise SignInError("This app doesn't say how to sign in to it. Add it with a header token instead.")
    scopes = header_scope
    if scopes is None and resource_meta and resource_meta.get('scopes_supported'):
        scopes = ' '.join(str(s) for s in resource_meta['scopes_supported'])
    return {
        'authorize': str(metadata['authorization_endpoint']),
        'token': str(metadata['token_endpoint']),
        'registration': metadata.get('registration_endpoint'),
        'revoke': metadata.get('revocation_endpoint'),
        'issuer': str(metadata.get('issuer') or auth_server or ''),
        'cimd': metadata.get('client_id_metadata_document_supported') is True,
        'scope': scopes,
        'resource': str((resource_meta or {}).get('resource') or url),
    }


def client_metadata(base_url: str) -> dict[str, Any]:
    """This engine as an OAuth client (served as its client ID metadata document)."""
    return {
        'client_id': cimd_url(base_url),
        'client_name': 'Cognitrix',
        'client_uri': base_url,
        'redirect_uris': [callback_url(base_url)],
        'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'],
        'token_endpoint_auth_method': 'none',
    }


def callback_url(base_url: str) -> str:
    from cognitrix.config import API_VERSION

    return f'{base_url}/api/{API_VERSION}/mcp/oauth/callback'


def provider_callback_url(base_url: str, provider: str) -> str:
    from cognitrix.config import API_VERSION

    return f'{base_url}/api/{API_VERSION}/auth/oauth/{provider}/callback'


def cimd_url(base_url: str) -> str:
    from cognitrix.config import API_VERSION

    return f'{base_url}/api/{API_VERSION}/mcp/oauth/client.json'


def _cimd_possible(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and host not in ('localhost', '127.0.0.1', '::1') and '.' in host


async def _register(meta: dict[str, Any], base_url: str, *, allow_private: bool) -> dict[str, Any]:
    redirect = callback_url(base_url)
    if meta['cimd'] and _cimd_possible(base_url):
        return {'client_id': cimd_url(base_url), 'client_secret': None, 'auth_method': 'none',
                'issuer': meta['issuer'], 'redirect_uri': redirect}
    endpoint = meta.get('registration')
    if not endpoint:
        raise SignInError(
            "This app doesn't let new apps register. Ask its admin for a client ID, "
            "or add it with a header token instead."
        )
    body = client_metadata(base_url)
    body.pop('client_id', None)
    body.pop('client_uri', None)
    try:
        await netcheck.check_url(str(endpoint), allow_private=allow_private)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(str(endpoint), json=body, headers={'Accept': 'application/json'})
        data = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError, netcheck.UnsafeURL):
        raise SignInError("Couldn't register Cognitrix with the app. Try again in a moment.")
    if response.status_code not in (200, 201) or not isinstance(data, dict) or not data.get('client_id'):
        raise SignInError("The app didn't accept Cognitrix as a new client.")
    return {
        'client_id': str(data['client_id']),
        'client_secret': data.get('client_secret') or None,
        'auth_method': str(data.get('token_endpoint_auth_method') or ('client_secret_post' if data.get('client_secret') else 'none')),
        'issuer': meta['issuer'],
        'redirect_uri': redirect,
    }


# --- the sign-in flow ---------------------------------------------------------

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


async def start_sign_in(
    row: Any, user_key: str, base_url: str, *, allow_private: bool = False,
    client_id: str | None = None, client_secret: str | None = None,
) -> dict[str, str]:
    """Begin signing ``row`` in. Returns ``{'flow_id', 'authorize_url'}``."""
    _prune()
    base_url = base_url.rstrip('/')
    data = load(row)
    app = catalog.get(getattr(row, 'catalog_id', None))
    params: dict[str, str] = {}

    if app and app.auth == 'provider':
        creds = catalog.provider_credentials(app.provider)
        if creds is None:
            raise SignInError(f'{app.name} is not set up on this server yet. {catalog.setup_hint(app)}')
        endpoints = PROVIDER_ENDPOINTS[app.provider or '']
        authorize, token_endpoint = endpoints['authorize'], endpoints['token']
        redirect = provider_callback_url(base_url, app.provider or '')
        client = {'client_id': creds[0], 'client_secret': creds[1], 'auth_method': 'client_secret_post'}
        scope = ' '.join(app.scopes)
        resource = None
        params.update(app.extra_params)
        if app.provider == 'google' and getattr(row, 'account', None):
            params['login_hint'] = str(row.account)
    else:
        meta = await discover(str(row.url or ''), allow_private=allow_private)
        authorize, token_endpoint = meta['authorize'], meta['token']
        for url in (authorize, token_endpoint):
            try:
                await netcheck.check_url(url, allow_private=allow_private)
            except netcheck.UnsafeURL:
                raise SignInError("The app's sign-in page is on a private address.")
        redirect = callback_url(base_url)
        saved = data.get('client') or {}
        if client_id:
            client = {'client_id': client_id.strip(), 'client_secret': (client_secret or '').strip() or None,
                      'auth_method': 'client_secret_post' if client_secret else 'none',
                      'issuer': meta['issuer'], 'redirect_uri': redirect}
        elif saved.get('client_id') and saved.get('issuer') == meta['issuer'] and saved.get('redirect_uri') == redirect:
            client = saved
        else:
            client = await _register(meta, base_url, allow_private=allow_private)
        scope = meta['scope']
        resource = meta['resource']
        data['client'] = client
        data['endpoints'] = {'token': token_endpoint, 'revoke': meta.get('revoke')}
        data['resource'] = resource
        data['allow_private'] = allow_private
        await _save(str(row.id), data)

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    flow = _Flow(
        state=state,
        flow_id=secrets.token_urlsafe(18),
        user_key=str(user_key),
        server_id=str(row.id),
        app_name=app.name if app else str(row.name),
        verifier=verifier,
        redirect_uri=redirect,
        token_endpoint=token_endpoint,
        client_id=client['client_id'],
        client_secret=client.get('client_secret'),
        auth_method=client.get('auth_method') or 'none',
        resource=resource,
        provider=app.provider if app and app.auth == 'provider' else None,
        allow_private=allow_private,
        created=time.time(),
    )
    _flows[state] = flow
    _results[flow.flow_id] = {'status': 'pending', 'error': None, 'user': flow.user_key,
                              'server_id': flow.server_id, 'created': flow.created}
    params.update({
        'response_type': 'code',
        'client_id': flow.client_id,
        'redirect_uri': redirect,
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    })
    if scope:
        params['scope'] = scope
    if resource:
        params['resource'] = resource
    separator = '&' if urlparse(authorize).query else '?'
    return {'flow_id': flow.flow_id, 'authorize_url': f'{authorize}{separator}{urlencode(params)}'}


def flow_status(flow_id: str, user_key: str) -> dict[str, Any] | None:
    _prune()
    result = _results.get(flow_id)
    if result is None or result['user'] != str(user_key):
        return None
    return {'status': result['status'], 'error': result['error'], 'server_id': result['server_id']}


def _token_request(flow_or_client: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    """Client authentication for the token endpoint."""
    secret = flow_or_client.get('client_secret')
    method = flow_or_client.get('auth_method') or 'none'
    kwargs: dict[str, Any] = {'headers': {'Accept': 'application/json'}}
    form = {**form}
    if secret and method == 'client_secret_basic':
        kwargs['auth'] = (flow_or_client['client_id'], secret)
    else:
        form['client_id'] = flow_or_client['client_id']
        if secret:
            form['client_secret'] = secret
    kwargs['data'] = form
    return kwargs


def _tokens_from(data: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    expires_in = data.get('expires_in')
    try:
        expires_at = time.time() + float(expires_in) if expires_in not in (None, '') else None
    except (TypeError, ValueError):
        expires_at = None
    return {
        'access_token': str(data['access_token']),
        # Many servers only send a refresh token the first time.
        'refresh_token': data.get('refresh_token') or previous.get('refresh_token'),
        'expires_at': expires_at,
        'scope': data.get('scope') or previous.get('scope'),
        'token_type': data.get('token_type') or 'Bearer',
    }


async def _account_label(provider: str | None, access_token: str) -> str:
    headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
            if provider == 'google':
                info = (await client.get('https://openidconnect.googleapis.com/v1/userinfo')).json()
                return str(info.get('email') or '')
            if provider == 'github':
                info = (await client.get('https://api.github.com/user')).json()
                return str(info.get('login') or '')
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return ''


async def finish_sign_in(state: str | None, code: str | None, error: str | None = None) -> tuple[str, str | None]:
    """Complete a sign-in from its callback. Returns (app name, error message or None)."""
    from cognitrix.models.mcp_server import MCPServer

    _prune()
    flow = _flows.pop(state or '', None)
    if flow is None:
        return 'the app', 'This sign-in link has expired. Start again from Connected apps.'

    def fail(message: str) -> tuple[str, str]:
        _results[flow.flow_id] = {**_results.get(flow.flow_id, {}), 'status': 'error', 'error': message,
                                  'user': flow.user_key, 'server_id': flow.server_id, 'created': flow.created}
        return flow.app_name, message

    if error or not code:
        return fail('Sign-in was cancelled.')
    form = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': flow.redirect_uri,
        'code_verifier': flow.verifier,
    }
    if flow.resource:
        form['resource'] = flow.resource
    client = {'client_id': flow.client_id, 'client_secret': flow.client_secret, 'auth_method': flow.auth_method}
    try:
        await netcheck.check_url(flow.token_endpoint, allow_private=flow.allow_private)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
            response = await http.post(flow.token_endpoint, **_token_request(client, form))
        data = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError, netcheck.UnsafeURL):
        return fail(f"Couldn't reach {flow.app_name}. Please try again.")
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get('access_token'):
        return fail(f"{flow.app_name} didn't accept the sign-in. Please try again.")

    row = await MCPServer.get(flow.server_id)
    if row is None or str(row.owner_id or '') != flow.user_key:
        return fail('This app was removed while you were signing in.')
    stored = load(row)
    stored['tokens'] = _tokens_from(data, stored.get('tokens'))
    if flow.provider:
        stored['provider'] = flow.provider
    account = await _account_label(flow.provider, stored['tokens']['access_token']) or (row.account or '')
    await _save(flow.server_id, stored, status='connected', account=account, auth_type='oauth', enabled=True)
    await _disconnect(flow.server_id)
    _results[flow.flow_id] = {**_results.get(flow.flow_id, {}), 'status': 'done', 'error': None,
                              'user': flow.user_key, 'server_id': flow.server_id, 'created': flow.created}
    return flow.app_name, None


async def _disconnect(server_id: str) -> None:
    from cognitrix.mcp.client import get_dynamic_client

    client = await get_dynamic_client()
    if client.is_connected(server_id):
        await client.disconnect_from_server(server_id)


def callback_page(name: str, error: str | None):
    """The page the browser lands on after signing in."""
    from fastapi.responses import HTMLResponse

    title = f"Couldn't connect {name}" if error else f'{name} is connected'
    body = error or 'You can close this tab and go back to Cognitrix.'
    outcome = 'error' if error else 'done'
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0c0e;color:#e8e9eb;
font-family:system-ui,sans-serif}}main{{max-width:420px;padding:24px;text-align:center}}
h1{{font-size:20px;margin:0 0 8px}}p{{color:#9aa0a6;line-height:1.5}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p></main>
<script>try{{if(window.opener){{window.opener.postMessage({{type:'cognitrix-app-sign-in',outcome:'{outcome}'}},window.location.origin);setTimeout(function(){{window.close()}},{4000 if error else 800});}}}}catch(e){{}}</script>
</body></html>"""
    return HTMLResponse(page)


# --- using and refreshing tokens ---------------------------------------------

async def mark_needs_reauth(row: Any, reason: str = 'expired') -> None:
    """Record that the sign-in stopped working and tell the owner, once."""
    from cognitrix.models.mcp_server import MCPServer

    if (getattr(row, 'status', None) or '') == 'needs_reauth':
        return
    await MCPServer.update_one({'id': row.id}, {'status': 'needs_reauth'})
    row.status = 'needs_reauth'
    await _disconnect(str(row.id))
    if not row.owner_id:
        return
    name = app_name(row)
    try:
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            str(row.owner_id),
            f'Reconnect {name}',
            f'The sign-in for {name} {"was revoked" if reason == "revoked" else "expired"}. '
            f'Agents can’t use {name} until you reconnect it.',
            link=APPS_PAGE,
        )
    except Exception:
        logger.exception('Could not add the reconnect alert for %s', name)


async def _refresh(row: Any, data: dict[str, Any]) -> dict[str, Any]:
    tokens = data.get('tokens') or {}
    refresh_token = tokens.get('refresh_token')
    if not refresh_token:
        raise ReconnectNeeded(app_name(row))
    provider = data.get('provider')
    if provider:
        creds = catalog.provider_credentials(provider)
        if creds is None:
            raise ReconnectNeeded(app_name(row), 'setup')
        client = {'client_id': creds[0], 'client_secret': creds[1], 'auth_method': 'client_secret_post'}
        endpoint = PROVIDER_ENDPOINTS[provider]['token']
    else:
        client = data.get('client') or {}
        endpoint = (data.get('endpoints') or {}).get('token')
        if not client.get('client_id') or not endpoint:
            raise ReconnectNeeded(app_name(row))
        try:
            await netcheck.check_url(str(endpoint), allow_private=bool(data.get('allow_private')))
        except netcheck.UnsafeURL:
            raise ReconnectNeeded(app_name(row))
    form = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
    if data.get('resource'):
        form['resource'] = data['resource']
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
        response = await http.post(endpoint, **_token_request(client, form))
    payload = response.json() if response.content else {}
    if response.status_code >= 400 or not isinstance(payload, dict) or not payload.get('access_token'):
        if 400 <= response.status_code < 500 or (isinstance(payload, dict) and payload.get('error')):
            raise ReconnectNeeded(app_name(row))
        raise httpx.HTTPError(f'token refresh failed with {response.status_code}')
    data = {**data, 'tokens': _tokens_from(payload, tokens)}
    await _save(str(row.id), data, status='connected')
    return data


def _stale(tokens: dict[str, Any]) -> bool:
    expires_at = tokens.get('expires_at')
    return bool(expires_at) and float(expires_at) - time.time() < REFRESH_MARGIN


def expires_at(row: Any) -> float | None:
    value = (load(row).get('tokens') or {}).get('expires_at')
    return float(value) if value else None


async def access_token(row: Any) -> str:
    """A working access token for ``row``, refreshed if it's about to expire.

    Raises ``ReconnectNeeded`` when the person has to sign in again (and marks
    the row), or ``httpx.HTTPError`` when the app couldn't be reached.
    """
    from cognitrix.models.mcp_server import MCPServer

    if (getattr(row, 'status', None) or '') == 'needs_reauth':
        raise ReconnectNeeded(app_name(row))
    data = load(row)
    tokens = data.get('tokens') or {}
    if not tokens.get('access_token'):
        raise ReconnectNeeded(app_name(row), 'never')
    if not _stale(tokens):
        return str(tokens['access_token'])

    lock = _refresh_locks.setdefault(str(row.id), asyncio.Lock())
    async with lock:
        # Another call (or process) may have refreshed it meanwhile.
        fresh = await MCPServer.get(row.id) or row
        data = load(fresh)
        tokens = data.get('tokens') or {}
        if tokens.get('access_token') and not _stale(tokens):
            return str(tokens['access_token'])
        try:
            data = await _refresh(fresh, data)
        except ReconnectNeeded as exc:
            # A worker in another process may have used a rotating refresh
            # token first; its new token is in the database.
            latest = await MCPServer.get(row.id)
            latest_tokens = load(latest).get('tokens') or {} if latest else {}
            if latest_tokens.get('access_token') and not _stale(latest_tokens):
                return str(latest_tokens['access_token'])
            if exc.reason != 'setup':
                await mark_needs_reauth(fresh)
            raise
        return str(data['tokens']['access_token'])


async def sign_out(row: Any) -> None:
    """Forget the tokens (and revoke them where the app supports it)."""
    data = load(row)
    tokens = data.get('tokens') or {}
    revoke = PROVIDER_ENDPOINTS.get(data.get('provider') or '', {}).get('revoke') or (data.get('endpoints') or {}).get('revoke')
    token = tokens.get('refresh_token') or tokens.get('access_token')
    if revoke and token:
        try:
            await netcheck.check_url(str(revoke), allow_private=bool(data.get('allow_private')) or bool(data.get('provider')))
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
                await http.post(str(revoke), data={'token': token})
        except (httpx.HTTPError, netcheck.UnsafeURL):
            pass
    data.pop('tokens', None)
    await _save(str(row.id), data, status='', account='')
    await _disconnect(str(row.id))
