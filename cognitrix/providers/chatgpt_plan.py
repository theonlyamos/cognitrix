"""Run agents on a ChatGPT Plus or Pro plan instead of an API key.

This follows OpenAI's "Sign in with ChatGPT" flow for open-source and
self-hosted apps (https://developers.openai.com/siwc/token-sharing-open-source):

1. The engine registers itself with OpenAI the first time someone signs in
   (``client_id=dynamic_agent_client`` plus a stable host id), and keeps the
   ``client_id`` OpenAI issues for later sign-ins to the same account.
2. The browser comes back to a short-lived listener on ``127.0.0.1``. When the
   browser is on another computer (a self-hosted server), the person pastes
   the address the browser ended up on instead.
3. The access token goes to the public Responses API at
   ``https://api.openai.com/v1/responses`` with ``store: false`` and
   ``stream: true``. Usage counts against the person's plan, not an API bill.

One ChatGPT account is connected per engine, by an admin, like a saved
provider key. Only the person who connected it can run agents on it: on a
shared server other people's chats are refused, because a ChatGPT plan is for
its owner. The credentials live sealed in ``<data folder>/chatgpt_plan.json``.

The provider name is ``chatgpt``. Agents keep the placeholder
``TOKEN_PLACEHOLDER`` as their api_key; the real token is read here just
before each request and refreshed when it is about to expire.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from cognitrix.errors import ExecutionControlError
from cognitrix.utils.llm_response import LLMResponse

logger = logging.getLogger('cognitrix.log')

PROVIDER = 'chatgpt'
API_BASE = 'https://api.openai.com/v1'
ISSUER = 'https://auth.openai.com'
AUTHORIZE_URL = f'{ISSUER}/api/accounts/authorize'
TOKEN_URL = f'{ISSUER}/api/accounts/oauth/token'
REVOKE_URL = f'{ISSUER}/api/accounts/oauth/revoke'
JWKS_URL = f'{ISSUER}/.well-known/jwks.json'
USAGE_URL = 'https://chatgpt.com/settings/usage'

APP_NAME = 'Cognitrix'
DYNAMIC_CLIENT = 'dynamic_agent_client'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
SCOPES = f'openid profile email offline_access resource.invoke {PLAN_SCOPE}'
TOKEN_PLACEHOLDER = 'chatgpt-plan'
"""Stands in for an API key in agent configs. Never a secret."""

CALLBACK_PATH = '/auth/callback'
PREFERRED_PORT = 1455
FLOW_TTL = 10 * 60
REFRESH_MARGIN = 5 * 60
HTTP_TIMEOUT = 20
NAMESPACE = 'cognitrix'
"""Tools go to the model inside one namespace, as the plan route requires."""


class ChatGPTPlanError(RuntimeError):
    """Something the person needs to see, worded for them."""


class NotSignedIn(ChatGPTPlanError):
    pass


# --- stored state --------------------------------------------------------------

_lock = threading.RLock()


def state_file() -> Path:
    from cognitrix.config import settings

    return Path(settings.workdir) / 'chatgpt_plan.json'


def _lock_file() -> Path:
    return state_file().with_suffix('.lock')


def _read() -> dict[str, Any]:
    from cognitrix.common.secret_box import unseal_or_empty

    try:
        raw = json.loads(state_file().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Couldn't read the ChatGPT plan sign-in at %s: %s", state_file(), exc)
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get('data'), str):
        return {}
    try:
        data = json.loads(unseal_or_empty(raw['data']) or '{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    from cognitrix.common.secret_box import seal

    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.chatgpt_plan.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump({'v': 1, 'data': seal(json.dumps(data))}, fh)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _FileLock:
    """Serialises token refreshes across processes (web server, worker).

    The refresh token rotates: two processes refreshing with the same one
    would end the session for both.
    """

    STALE = 60.0

    def __init__(self) -> None:
        self.path = _lock_file()
        self.fd: int | None = None

    def acquire(self, timeout: float = 30.0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                return
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.STALE:
                        os.unlink(self.path)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise ChatGPTPlanError('Another part of the engine is renewing the ChatGPT sign-in. Try again.')
                time.sleep(0.1)

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


def host_id() -> str:
    """This engine's stable, opaque host id; created once and kept."""
    with _lock:
        data = _read()
        value = data.get('host_id')
        if isinstance(value, str) and value.startswith('urn:uuid:'):
            return value
        value = f'urn:uuid:{uuid.uuid4()}'
        data['host_id'] = value
        _write(data)
        return value


def _session() -> dict[str, Any] | None:
    session = _read().get('session')
    return session if isinstance(session, dict) and session.get('access_token') else None


def status() -> dict[str, Any]:
    """What Settings shows. Never a token."""
    data = _read()
    session = _session()
    last = data.get('registrations', {}).get(data.get('last_subject') or '') or {}
    out: dict[str, Any] = {
        'signed_in': bool(session),
        'email': (session or {}).get('email') or None,
        'name': (session or {}).get('name') or None,
        'owner_id': (session or {}).get('owner_id') or None,
        'needs_sign_in': data.get('needs_sign_in') or None,
        'previous_email': None if session else (last.get('email') or None),
        'usage_url': USAGE_URL,
    }
    return out


# --- sign-in -------------------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


@dataclass
class Flow:
    id: str
    owner_id: str
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    client_id: str
    """The issued id for a returning account, else DYNAMIC_CLIENT."""
    expected_subject: str | None
    created: float = field(default_factory=time.time)
    status: str = 'pending'
    error: str | None = None
    server: Any = None
    authorize_url: str = ''


_flows: dict[str, Flow] = {}


def _prune() -> None:
    now = time.time()
    for flow_id, flow in list(_flows.items()):
        if now - flow.created > FLOW_TTL * 2:
            _close_listener(flow)
            _flows.pop(flow_id, None)


def _close_listener(flow: Flow) -> None:
    server, flow.server = flow.server, None
    if server is not None:
        try:
            server.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass


_DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Cognitrix</title>
<style>body{{font:15px system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#222}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}}}</style>
<h1 style="font-size:20px">{title}</h1><p>{body}</p>"""


async def _listen(flow: Flow) -> tuple[Any, int]:
    """Start the loopback listener for this flow; (server, port)."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode('latin-1')
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b'\r\n', b'\n', b''):
                pass
            parts = line.split(' ')
            target = parts[1] if len(parts) > 1 else '/'
            if urlsplit(target).path != CALLBACK_PATH:
                writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                return
            await finish_sign_in(flow.id, dict(_query(target)))
            if flow.status == 'done':
                page = _DONE_PAGE.format(title='Signed in to ChatGPT',
                                         body='Cognitrix can now use your ChatGPT plan. You can close this tab.')
            else:
                page = _DONE_PAGE.format(title='ChatGPT sign-in did not finish',
                                         body=_html_escape(flow.error or 'Something went wrong.'))
            body = page.encode()
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n'
                + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body
            )
        except Exception as exc:  # noqa: BLE001 - the browser just sees a closed tab
            logger.warning('ChatGPT sign-in callback failed: %s', exc)
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    for port in (PREFERRED_PORT, 0):
        try:
            server = await asyncio.start_server(handle, '127.0.0.1', port)
        except OSError:
            continue
        return server, server.sockets[0].getsockname()[1]
    raise ChatGPTPlanError("Couldn't open a local port for the ChatGPT sign-in.")


def _html_escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _query(url: str) -> list[tuple[str, str]]:
    return [(k, v[0]) for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items() if v]


async def start_sign_in(owner_id: str, *, new_account: bool = False) -> dict[str, Any]:
    """Begin a sign-in. Returns {flow_id, authorize_url, redirect_uri}."""
    _prune()
    data = _read()
    registrations = data.get('registrations') if isinstance(data.get('registrations'), dict) else {}
    previous = None if new_account else registrations.get(data.get('last_subject') or '')
    flow = Flow(
        id=secrets.token_urlsafe(18),
        owner_id=owner_id,
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier='',
        redirect_uri='',
        client_id=(previous or {}).get('client_id') or DYNAMIC_CLIENT,
        expected_subject=data.get('last_subject') if previous else None,
    )
    flow.verifier, challenge = _pkce()
    flow.server, port = await _listen(flow)
    flow.redirect_uri = f'http://127.0.0.1:{port}{CALLBACK_PATH}'
    params = {
        'client_id': flow.client_id,
        'ext_agent_host_id': host_id(),
        'response_type': 'code',
        'redirect_uri': flow.redirect_uri,
        'scope': SCOPES,
        'resource': API_BASE,
        'state': flow.state,
        'nonce': flow.nonce,
        'code_challenge_method': 'S256',
        'code_challenge': challenge,
    }
    if flow.client_id == DYNAMIC_CLIENT:
        params['agent_name_hint'] = APP_NAME
    else:
        if previous.get('email'):
            params['login_hint'] = previous['email']
        if previous.get('id_token'):
            params['id_token_hint'] = previous['id_token']
    flow.authorize_url = f'{AUTHORIZE_URL}?{urlencode(params)}'
    _flows[flow.id] = flow
    asyncio.get_running_loop().call_later(FLOW_TTL, _expire, flow.id)
    return {'flow_id': flow.id, 'authorize_url': flow.authorize_url, 'redirect_uri': flow.redirect_uri}


def _expire(flow_id: str) -> None:
    flow = _flows.get(flow_id)
    if flow is None:
        return
    _close_listener(flow)
    if flow.status == 'pending':
        flow.status, flow.error = 'error', 'Sign-in took too long. Please try again.'


def flow_status(flow_id: str, owner_id: str) -> dict[str, Any] | None:
    flow = _flows.get(flow_id)
    if flow is None or flow.owner_id != owner_id:
        return None
    return {'status': flow.status, 'error': flow.error}


async def finish_from_pasted_url(flow_id: str, owner_id: str, url: str) -> dict[str, Any] | None:
    """The person pasted the address the browser landed on (remote engine)."""
    flow = _flows.get(flow_id)
    if flow is None or flow.owner_id != owner_id:
        return None
    url = (url or '').strip()
    if not url:
        raise ChatGPTPlanError('Paste the address from your browser first.')
    params = dict(_query(url))
    if 'state' not in params:
        raise ChatGPTPlanError("That address doesn't look like the one ChatGPT sent you back to.")
    await finish_sign_in(flow_id, params)
    return {'status': flow.status, 'error': flow.error}


async def finish_sign_in(flow_id: str, params: dict[str, str]) -> None:
    flow = _flows.get(flow_id)
    if flow is None or flow.status != 'pending':
        return
    if not secrets.compare_digest(str(params.get('state') or ''), flow.state):
        # Not ours (a stale tab): ignore it and keep waiting.
        logger.warning('ChatGPT sign-in callback with an unknown state was ignored.')
        return

    def fail(message: str) -> None:
        flow.status, flow.error = 'error', message
        _close_listener(flow)

    error = params.get('error')
    if error:
        fail('You declined the ChatGPT sign-in.' if error == 'access_denied'
             else f"ChatGPT sign-in failed: {params.get('error_description') or error}.")
        return
    code = params.get('code')
    if not code:
        fail('ChatGPT did not send a sign-in code back.')
        return
    returned_client = params.get('client_id') or ''
    if flow.client_id == DYNAMIC_CLIENT:
        if not returned_client or returned_client == DYNAMIC_CLIENT:
            fail("ChatGPT didn't finish registering Cognitrix. Please try again.")
            return
        client_id = returned_client
    else:
        if returned_client and returned_client != flow.client_id:
            fail('ChatGPT answered for a different registration. Please try again.')
            return
        client_id = flow.client_id
    try:
        tokens = await _token_request({
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'code': code,
            'code_verifier': flow.verifier,
            'redirect_uri': flow.redirect_uri,
            'resource': API_BASE,
        })
        claims = await asyncio.to_thread(_verify_id_token, tokens.get('id_token') or '', client_id, flow.nonce)
    except ChatGPTPlanError as exc:
        fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - shown to the person
        logger.warning('ChatGPT sign-in exchange failed: %s', exc)
        fail("Couldn't finish the ChatGPT sign-in. Please try again.")
        return
    subject = str(claims.get('sub') or '')
    if flow.expected_subject and subject != flow.expected_subject:
        fail('You signed in to a different ChatGPT account. Use "Use another account" to switch.')
        return
    scopes = str(tokens.get('scope') or '').split()
    record = _record(tokens, claims, client_id, flow.owner_id)
    with _lock:
        data = _read()
        data.setdefault('host_id', host_id())
        registrations = data.get('registrations') if isinstance(data.get('registrations'), dict) else {}
        registrations[subject] = {
            'client_id': client_id,
            'email': record.get('email') or '',
            'id_token': record.get('id_token') or '',
        }
        data['registrations'] = registrations
        data['last_subject'] = subject
        data.pop('needs_sign_in', None)
        if PLAN_SCOPE in scopes:
            data['session'] = record
        else:
            data.pop('session', None)
        _write(data)
    _forget_cached_llms()
    if PLAN_SCOPE not in scopes:
        fail('You signed in, but ChatGPT plan use was not allowed. Sign in again and allow it, or use an API key.')
        return
    flow.status = 'done'
    _close_listener(flow)


def _record(tokens: dict[str, Any], claims: dict[str, Any], client_id: str, owner_id: str) -> dict[str, Any]:
    now = time.time()
    expires_in = _int(tokens.get('expires_in'), 3600)
    return {
        'subject': str(claims.get('sub') or ''),
        'email': str(claims.get('email') or ''),
        'name': str(claims.get('name') or ''),
        'client_id': client_id,
        'owner_id': owner_id,
        'id_token': tokens.get('id_token') or '',
        'access_token': tokens.get('access_token') or '',
        'refresh_token': tokens.get('refresh_token') or '',
        'expires_at': now + expires_in,
        'earliest_refresh_at': _int(tokens.get('earliest_refresh_at'), 0) or 0,
        'scopes': str(tokens.get('scope') or '').split(),
        'saved_at': now,
    }


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _token_request(form: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(TOKEN_URL, data=form, headers={'Accept': 'application/json'})
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200:
        code = str(body.get('error') or response.status_code) if isinstance(body, dict) else str(response.status_code)
        raise _TokenError(code)
    if not isinstance(body, dict) or not body.get('access_token'):
        raise ChatGPTPlanError('ChatGPT sent back an empty sign-in.')
    return body


class _TokenError(ChatGPTPlanError):
    def __init__(self, code: str):
        super().__init__(f'ChatGPT refused the sign-in ({code}).')
        self.code = code


_jwks_client: Any = None


def _verify_id_token(id_token: str, client_id: str, nonce: str) -> dict[str, Any]:
    import jwt

    global _jwks_client
    if not id_token:
        raise ChatGPTPlanError('ChatGPT did not send an ID token.')
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URL, cache_keys=True)
    key = _jwks_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token, key.key, algorithms=['RS256', 'ES256'], audience=client_id, issuer=ISSUER,
        options={'require': ['exp', 'iss', 'aud', 'sub']},
    )
    if not secrets.compare_digest(str(claims.get('nonce') or ''), nonce):
        raise ChatGPTPlanError("The ChatGPT sign-in didn't match this request. Please try again.")
    return claims


async def sign_out() -> None:
    """Revoke the renewable session and forget the tokens.

    The registration (client id per account) and the host id are kept, so
    signing in again does not register a new app. Holds the refresh lock so
    a renewal running at the same time can't bring the session back.
    """
    lock = _FileLock()
    await asyncio.to_thread(lock.acquire)
    try:
        await _sign_out_locked()
    finally:
        lock.release()


async def _sign_out_locked() -> None:
    session = _session()
    revoked = True
    if session and session.get('refresh_token'):
        revoked = False
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    response = await client.post(REVOKE_URL, data={
                        'token': session['refresh_token'],
                        'token_type_hint': 'refresh_token',
                        'client_id': session.get('client_id') or '',
                    })
                if response.status_code < 500:
                    revoked = response.status_code == 200
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2 ** attempt)
    with _lock:
        data = _read()
        data.pop('session', None)
        data.pop('needs_sign_in', None)
        _write(data)
    _forget_cached_llms()
    if not revoked:
        raise ChatGPTPlanError(
            "Signed out here, but ChatGPT didn't confirm it. You can also disconnect Cognitrix in ChatGPT settings."
        )


def _forget_cached_llms() -> None:
    try:
        from cognitrix.providers.base import LLMManager

        LLMManager._llm_cache = {}
    except Exception:  # noqa: BLE001
        pass


def _mark_needs_sign_in(reason: str) -> None:
    with _lock:
        data = _read()
        session = data.pop('session', None)
        data['needs_sign_in'] = reason
        if isinstance(session, dict) and session.get('subject'):
            # Keep the id token as a hint for the next sign-in.
            reg = data.setdefault('registrations', {}).setdefault(session['subject'], {})
            reg.setdefault('client_id', session.get('client_id') or '')
            reg['id_token'] = session.get('id_token') or reg.get('id_token') or ''
        _write(data)


_REFRESH_DEAD = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                 'refresh_token_invalidated', 'refresh_token_reused'}


def check_owner(owner_id: str | None) -> None:
    """Refuse when someone else's chat would spend this ChatGPT plan."""
    session = _session()
    if session is None:
        return
    signed_in_by = session.get('owner_id') or ''
    if owner_id and signed_in_by and owner_id != signed_in_by:
        raise ChatGPTPlanError(
            'This ChatGPT plan belongs to the person who connected it, so only their chats can use it. '
            'Pick another provider for this agent.'
        )


async def access_token(*, force_refresh: bool = False) -> str:
    """A current access token, refreshed when it is close to expiring."""
    session = _session()
    if session is None:
        reason = _read().get('needs_sign_in')
        raise NotSignedIn(
            'The ChatGPT sign-in has expired. Sign in again in Settings › Models and keys.' if reason
            else 'No ChatGPT account is connected. Sign in under Settings › Models and keys.'
        )
    if not force_refresh and session.get('expires_at', 0) - time.time() > REFRESH_MARGIN:
        return session['access_token']
    return await asyncio.to_thread(_refresh_blocking, session['access_token'], force_refresh)


def _refresh_blocking(stale_token: str, force: bool) -> str:
    lock = _FileLock()
    lock.acquire()
    try:
        session = _session()
        if session is None:
            raise NotSignedIn('No ChatGPT account is connected. Sign in under Settings › Models and keys.')
        # Another process may have refreshed while we waited for the lock.
        if session['access_token'] != stale_token and session.get('expires_at', 0) - time.time() > REFRESH_MARGIN:
            return session['access_token']
        if not force and session.get('expires_at', 0) > time.time() and time.time() < session.get('earliest_refresh_at', 0):
            return session['access_token']
        used_refresh = session.get('refresh_token')
        try:
            response = httpx.post(TOKEN_URL, data={
                'grant_type': 'refresh_token',
                'client_id': session.get('client_id') or '',
                'refresh_token': session.get('refresh_token') or '',
                'resource': API_BASE,
            }, headers={'Accept': 'application/json'}, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            if session.get('expires_at', 0) > time.time():
                return session['access_token']
            raise ChatGPTPlanError(f"Couldn't reach ChatGPT to renew the sign-in: {type(exc).__name__}.") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not isinstance(body, dict) or not body.get('access_token'):
            code = str(body.get('error') or '') if isinstance(body, dict) else ''
            if code in _REFRESH_DEAD or response.status_code in (400, 401):
                _mark_needs_sign_in('expired')
                raise NotSignedIn('The ChatGPT sign-in has expired. Sign in again in Settings › Models and keys.')
            if code == 'invalid_client':
                _mark_needs_sign_in('invalid_client')
                raise NotSignedIn('ChatGPT no longer recognises this app. Sign in again in Settings › Models and keys.')
            if session.get('expires_at', 0) > time.time():
                return session['access_token']
            raise ChatGPTPlanError(f"ChatGPT couldn't renew the sign-in right now ({response.status_code}).")
        now = time.time()
        session.update({
            'access_token': body['access_token'],
            'refresh_token': body.get('refresh_token') or session.get('refresh_token'),
            'expires_at': now + _int(body.get('expires_in'), 3600),
            'earliest_refresh_at': _int(body.get('earliest_refresh_at'), 0) or 0,
            'saved_at': now,
        })
        if body.get('scope'):
            session['scopes'] = str(body['scope']).split()
        if body.get('id_token'):
            session['id_token'] = body['id_token']
        with _lock:
            data = _read()
            current = data.get('session')
            # A sign-out or new sign-in while this refresh ran wins: don't bring
            # back a session that is no longer the stored one.
            if not isinstance(current, dict) or current.get('refresh_token') != used_refresh:
                raise NotSignedIn('The ChatGPT sign-in changed while it was being renewed. Try again.')
            data['session'] = session
            _write(data)
        return session['access_token']
    finally:
        lock.release()


# --- models --------------------------------------------------------------------

async def list_models() -> list[dict[str, str]]:
    """[{slug, display_name}] the signed-in account may use, in OpenAI's order."""
    token = await access_token()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(f'{API_BASE}/models', headers={'Authorization': f'Bearer {token}'})
    if response.status_code in (401, 403):
        raise ChatGPTPlanError('ChatGPT refused the model list for this sign-in.')
    response.raise_for_status()
    body = response.json()
    rows = body.get('models') if isinstance(body, dict) else None
    if rows is None and isinstance(body, dict):
        rows = body.get('data')
    out: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get('visibility') not in (None, 'list'):
            continue
        slug = str(row.get('slug') or row.get('id') or '').strip()
        if slug and all(m['slug'] != slug for m in out):
            out.append({'slug': slug, 'display_name': str(row.get('display_name') or slug)})
    return out


# --- request conversion -------------------------------------------------------

def _text(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get('type') == 'text':
            parts.append(str(part.get('text') or ''))
    return '\n'.join(p for p in parts if p)


def _input_parts(content: Any) -> list[dict[str, Any]]:
    """Chat message content as Responses input parts (text and images)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{'type': 'input_text', 'text': content}] if content.strip() else []
    parts: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            if part.strip():
                parts.append({'type': 'input_text', 'text': part})
        elif isinstance(part, dict):
            if part.get('type') == 'text' and str(part.get('text') or '').strip():
                parts.append({'type': 'input_text', 'text': str(part['text'])})
            elif part.get('type') == 'image_url':
                image = part.get('image_url')
                url = image.get('url', '') if isinstance(image, dict) else str(image or '')
                if url:
                    parts.append({'type': 'input_image', 'image_url': url, 'detail': 'auto'})
    return parts


def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    functions = []
    for tool in tools or []:
        fn = tool.get('function') if isinstance(tool, dict) else None
        if not fn or not fn.get('name'):
            continue
        functions.append({
            'type': 'function',
            'name': fn['name'],
            'description': fn.get('description') or '',
            'parameters': fn.get('parameters') or {'type': 'object', 'properties': {}},
        })
    if not functions:
        return []
    return [{
        'type': 'namespace',
        'name': NAMESPACE,
        'description': "The agent's tools.",
        'tools': functions,
    }]


def convert_messages(messages: list[dict[str, Any]], with_tools: bool) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI chat messages -> (instructions, Responses input items).

    System messages before the conversation become the instructions; later
    ones become developer messages (the plan route rejects system items).
    Every function call gets an output, and an output with no call becomes
    plain text, since the API rejects unmatched pairs. Without tools in the
    request, earlier tool calls are written out as text.
    """
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    started = False
    waiting: list[str] = []

    def close_waiting() -> None:
        for call_id in waiting:
            items.append({'type': 'function_call_output', 'call_id': call_id, 'output': '(no result was recorded)'})
        waiting.clear()

    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content')
        if role == 'system':
            text = _text(content)
            if not text:
                continue
            if not started:
                instructions.append(text)
            else:
                close_waiting()
                items.append({'type': 'message', 'role': 'developer',
                              'content': [{'type': 'input_text', 'text': text}]})
            continue
        started = True
        if role == 'tool':
            call_id = str(msg.get('tool_call_id') or '')
            output = _text(content) or '(no output)'
            if with_tools and call_id in waiting:
                waiting.remove(call_id)
                items.append({'type': 'function_call_output', 'call_id': call_id, 'output': output})
            else:
                close_waiting()
                items.append({'type': 'message', 'role': 'user',
                              'content': [{'type': 'input_text', 'text': f'[Tool result]\n{output}'}]})
            continue
        if role == 'assistant':
            close_waiting()
            text = _text(content)
            calls = msg.get('tool_calls') or []
            if text.strip():
                items.append({'type': 'message', 'role': 'assistant',
                              'content': [{'type': 'output_text', 'text': text}]})
            for i, call in enumerate(calls):
                fn = call.get('function') or {}
                name = str(fn.get('name') or 'tool')
                arguments = fn.get('arguments') or '{}'
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments)
                call_id = str(call.get('id') or f'call_{i}')
                if with_tools:
                    items.append({'type': 'function_call', 'call_id': call_id, 'name': name,
                                  'namespace': NAMESPACE, 'arguments': arguments})
                    waiting.append(call_id)
                else:
                    items.append({'type': 'message', 'role': 'assistant', 'content': [
                        {'type': 'output_text', 'text': f'[Called {name} with {arguments}]'}]})
            continue
        close_waiting()
        parts = _input_parts(content)
        if parts:
            items.append({'type': 'message', 'role': 'user', 'content': parts})
    close_waiting()
    if not items or items[-1].get('role') == 'assistant':
        items.append({'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Continue.'}]})
    return '\n\n'.join(instructions), items


_EFFORTS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}


def _text_format(response_format: Any) -> dict[str, Any] | None:
    if not isinstance(response_format, dict):
        return None
    kind = response_format.get('type')
    if kind == 'json_object':
        return {'type': 'json_object'}
    if kind == 'json_schema':
        schema = response_format.get('json_schema') or {}
        fmt: dict[str, Any] = {'type': 'json_schema', 'name': schema.get('name') or 'response',
                               'schema': schema.get('schema') or {}}
        if 'strict' in schema:
            fmt['strict'] = bool(schema['strict'])
        return fmt
    return None


def build_params(llm: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                 effort: str = '', response_format: Any = None) -> dict[str, Any]:
    """A Responses request that keeps to the plan route's rules.

    No temperature, max_output_tokens, metadata or previous_response_id: the
    plan route rejects them. store=false and stream=true are required.
    """
    namespaced = convert_tools(tools)
    instructions, items = convert_messages(messages, with_tools=bool(namespaced))
    params: dict[str, Any] = {
        'model': llm.model,
        'input': items,
        'store': False,
        'stream': True,
    }
    if instructions:
        params['instructions'] = instructions
    if namespaced:
        params['tools'] = namespaced
    reasoning: dict[str, Any] = {'summary': 'auto'}
    if effort in _EFFORTS:
        reasoning['effort'] = effort
    params['reasoning'] = reasoning
    fmt = _text_format(response_format)
    if fmt:
        params['text'] = {'format': fmt}
    return params


# --- calling -------------------------------------------------------------------

_USAGE_LIMIT_CODES = {'subscription_sharing_usage_limit_exceeded'}
_RETRY_LATER_CODES = {'subscription_sharing_usage_unavailable', 'subscription_sharing_user_unavailable'}


def _error_message(code: str, detail: str = '') -> str:
    if code in _USAGE_LIMIT_CODES:
        return (f'Your ChatGPT plan has reached its usage limit for Cognitrix. '
                f'Check or raise it at {USAGE_URL}, or switch this agent to an API key provider.')
    if code == 'subscription_sharing_user_not_eligible':
        return ('ChatGPT plan use is not available for this account or workspace. '
                'It needs a ChatGPT Plus or Pro plan. Use an API key provider instead.')
    if code in _RETRY_LATER_CODES:
        return 'ChatGPT could not check your plan usage just now. Try again in a minute.'
    if code == 'subscription_sharing_unsupported_capability':
        return f'The ChatGPT plan does not support part of this request{": " + detail if detail else ""}.'
    if code == 'subscription_sharing_invalid_user':
        return 'ChatGPT could not confirm this sign-in. Sign in again in Settings › Models and keys.'
    if code:
        return f'ChatGPT plan request failed ({code}){": " + detail if detail else ""}.'
    return f'ChatGPT plan request failed{": " + detail if detail else ""}.'


def _api_error(exc: Exception) -> tuple[int | None, str, str]:
    """(status, code, detail) from an openai APIStatusError."""
    status = getattr(exc, 'status_code', None)
    body = getattr(exc, 'body', None)
    code = ''
    detail = ''
    if isinstance(body, dict):
        err = body.get('error') if isinstance(body.get('error'), dict) else body
        code = str(err.get('code') or '')
        detail = str(err.get('message') or err.get('detail') or err.get('param') or '')
    return status, code, detail


def _client(token: str):
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=token, base_url=API_BASE, max_retries=0, timeout=600)


async def _open_stream(params: dict[str, Any]):
    """responses.create(stream=True) with refresh-on-401 and bounded retries."""
    import openai

    from cognitrix.providers import base
    from cognitrix.tasks.accounting import current_task_accounting

    accounting = current_task_accounting()
    attempt = 0
    refreshed = False
    while True:
        attempt += 1
        if attempt > 1 and accounting is not None:
            await accounting.begin_provider_retry()
        token = await access_token(force_refresh=refreshed and attempt > 1)
        try:
            return await _client(token).responses.create(**params)
        except openai.AuthenticationError:
            if refreshed:
                raise ChatGPTPlanError('ChatGPT refused this sign-in. Sign in again in Settings › Models and keys.')
            refreshed = True
            if accounting is not None:
                await accounting.finish_failed_provider_attempt()
            continue
        except openai.APIStatusError as exc:
            status, code, detail = _api_error(exc)
            transient = (status is not None and status >= 500 and code not in _USAGE_LIMIT_CODES) or (
                status == 429 and code not in _USAGE_LIMIT_CODES)
            if not transient or attempt >= base._MAX_ATTEMPTS:
                raise ChatGPTPlanError(_error_message(code, detail)) from exc
            if accounting is not None:
                await accounting.finish_failed_provider_attempt()
            delay = base._retry_delay(exc, attempt)
            logger.warning('ChatGPT plan request failed (attempt %s, retrying in %.1fs): %s', attempt, delay, exc)
            await asyncio.sleep(delay)
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            if attempt >= base._MAX_ATTEMPTS:
                raise ChatGPTPlanError(f"Couldn't reach ChatGPT: {type(exc).__name__}.") from exc
            if accounting is not None:
                await accounting.finish_failed_provider_attempt()
            await asyncio.sleep(base._retry_delay(exc, attempt))


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _usage(raw: Any) -> dict[str, int]:
    details = _field(raw, 'input_tokens_details')
    cached = _field(details, 'cached_tokens', 0) if details is not None else 0
    counts = {
        'prompt_tokens': int(_field(raw, 'input_tokens', 0) or 0),
        'completion_tokens': int(_field(raw, 'output_tokens', 0) or 0),
    }
    if cached:
        counts['cached_tokens'] = int(cached)
    return counts


async def stream(llm: Any, params: dict[str, Any]) -> AsyncIterator[LLMResponse]:
    """A streamed reply, yielded the way base.py's OpenAI stream handler does."""
    from cognitrix.providers.base import _malformed_arguments_error, record_usage

    try:
        from cognitrix import usage as usage_log

        owner = usage_log._owner.get()
        check_owner((owner or {}).get('user_id'))
        events = await _open_stream(params)
        response = LLMResponse()
        in_reasoning = False
        calls: list[dict[str, Any]] = []
        finish_reason: str | None = None
        completed = False
        failure: str | None = None
        async for event in events:
            kind = _field(event, 'type', '')
            if kind in ('response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
                delta = _field(event, 'delta', '') or ''
                if delta:
                    response.add_reasoning_chunk(delta)
                    response.current_chunk = delta if in_reasoning else '<think>' + delta
                    in_reasoning = True
                    yield response
            elif kind == 'response.output_text.delta':
                delta = _field(event, 'delta', '') or ''
                if delta:
                    response.add_chunk(delta)
                    response.current_chunk = ('\n</think>\n' + delta) if in_reasoning else delta
                    in_reasoning = False
                    yield response
            elif kind == 'response.output_item.done':
                item = _field(event, 'item')
                if _field(item, 'type') == 'function_call':
                    calls.append({
                        'name': str(_field(item, 'name', '') or ''),
                        'arguments': _field(item, 'arguments', '') or '{}',
                        'tool_call_id': _field(item, 'call_id') or _field(item, 'id'),
                    })
            elif kind in ('response.completed', 'response.incomplete'):
                completed = kind == 'response.completed'
                resp = _field(event, 'response')
                raw_usage = _field(resp, 'usage')
                if raw_usage is not None:
                    response.usage = _usage(raw_usage)
                if kind == 'response.incomplete':
                    reason = _field(_field(resp, 'incomplete_details'), 'reason', '')
                    finish_reason = 'length' if reason == 'max_output_tokens' else 'content_filter'
            elif kind == 'response.failed':
                err = _field(_field(event, 'response'), 'error')
                failure = _error_message(str(_field(err, 'code', '') or ''), str(_field(err, 'message', '') or ''))
            elif kind == 'error':
                failure = _error_message(str(_field(event, 'code', '') or ''), str(_field(event, 'message', '') or ''))
        if failure or (not completed and finish_reason is None):
            raise ChatGPTPlanError(failure or 'The ChatGPT reply stopped before it finished.')
        truncated = finish_reason == 'length'
        parsed = []
        for call in calls:
            if not call['name']:
                continue
            raw = call['arguments']
            item: dict[str, Any] = {'name': call['name'], 'arguments': {}, 'tool_call_id': call['tool_call_id']}
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(args, dict):
                    raise ValueError('arguments are not an object')
                item['arguments'] = args
            except ValueError:
                item['arguments_error'] = _malformed_arguments_error(call['name'], raw, truncated)
            parsed.append(item)
        if parsed:
            response.tool_calls = parsed
        response.finish_reason = finish_reason or ('tool_calls' if parsed else 'stop')
        if response.usage:
            record_usage(response.usage)
        response.current_chunk = '\n</think>\n' if in_reasoning else ''
        yield response
    except ExecutionControlError:
        raise
    except ChatGPTPlanError as exc:
        msg = str(exc)
        err = LLMResponse(llm_response=msg, error=msg)
        err.current_chunk = msg
        yield err
    except Exception as exc:  # noqa: BLE001 - shown in the chat
        logger.exception('Error in ChatGPT plan response: %s', exc)
        msg = f'Streaming error: {exc}'
        err = LLMResponse(llm_response=msg, error=msg)
        err.current_chunk = msg
        yield err


async def respond(llm: Any, params: dict[str, Any]) -> LLMResponse:
    """One whole reply. The plan route only streams, so this reads the stream."""
    last: LLMResponse | None = None
    async for item in stream(llm, params):
        last = item
    if last is None:
        msg = 'ChatGPT sent no reply.'
        return LLMResponse(llm_response=msg, error=msg)
    if last.reasoning and not last.error:
        # Same shape as the non-streaming OpenAI path.
        text = last.llm_response or ''
        out = LLMResponse()
        out.add_reasoning_chunk(last.reasoning)
        out.add_chunk(f'<think>{last.reasoning}</think>\n\n{text}')
        out.tool_calls = last.tool_calls
        out.usage = last.usage
        out.finish_reason = last.finish_reason
        return out
    last.current_chunk = ''
    return last
