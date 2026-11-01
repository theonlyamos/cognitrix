"""HTTP Request and List Credentials: call APIs with saved sign-ins.

The model refers to a saved credential by name; the value is filled in here,
right before the request is sent, and only for the credential's allowed
hosts. See ``cognitrix/credentials.py``.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urljoin, urlparse

from cognitrix.credentials import PLACEHOLDER_RE, Resolver, SecretUseError, describe, load_rows, redact
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

_METHODS = ('GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE')
_MAX_BODY_IN = 1_000_000
_MAX_REDIRECTS = 5
_SHOWN_HEADERS = ('content-type', 'content-length', 'location', 'retry-after', 'www-authenticate', 'x-ratelimit-remaining')


def _owner() -> str | None:
    context = current_execution_context()
    return str(context.user_id) if context.user_id else None


def _host(url: str) -> str:
    return urlparse(url).netloc.rsplit('@', 1)[-1].lower()


def _fill_json(value: Any, resolver: Resolver, host: str) -> Any:
    if isinstance(value, str):
        return resolver.fill(value, host)
    if isinstance(value, dict):
        return {k: _fill_json(v, resolver, host) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_json(v, resolver, host) for v in value]
    return value


def _mentions_secret(*values: Any) -> bool:
    text = json.dumps(values, default=str)
    return bool(PLACEHOLDER_RE.search(text))


@tool(category='web', retryable=False, max_attempts=1)
async def http_request(
    url: str,
    method: str = 'GET',
    headers: dict | None = None,
    body: str | None = None,
    json_body: dict | None = None,
    credential: str | None = None,
    max_length: int = 20000,
):
    """Send an HTTP request to an API, optionally signed in with a saved credential.

    Saved credentials (tokens, API keys, usernames and passwords) are never
    shown to you. Use List Credentials to see their names, then either:
    - set `credential` to the name, which adds the right Authorization header, or
    - write {{secret:NAME}} (or {{secret:NAME.username}} / {{secret:NAME.password}})
      inside the url, a header value or the body, and it is filled in when sent.
    A credential only works for the hosts it was saved for. Values that come back
    in a response are hidden as [NAME hidden].

    Args:
        url (str): Full http(s) address.
        method (str, optional): GET, POST, PUT, PATCH, DELETE, HEAD or OPTIONS. Defaults to GET.
        headers (dict, optional): Extra request headers.
        body (str, optional): Raw request body.
        json_body (dict, optional): JSON request body (sets Content-Type).
        credential (str, optional): Name of a saved credential to sign in with.
        max_length (int, optional): Maximum characters of the response body to return. Defaults to 20000.

    Returns:
        str: The status, a few useful response headers and the response body.
    """
    import httpx

    from cognitrix.utils.webhooks import check_public_url

    method = str(method or 'GET').upper()
    if method not in _METHODS:
        return ToolOutcome.failure('bad_method', f"Error: unsupported method {method}")
    if body is not None and json_body is not None:
        return ToolOutcome.failure('bad_body', 'Error: send either body or json_body, not both')
    if body is not None and len(body) > _MAX_BODY_IN:
        return ToolOutcome.failure('body_too_large', 'Error: the request body is too large')
    try:
        max_length = max(200, min(int(max_length), 200_000))
    except (TypeError, ValueError):
        max_length = 20000

    owner = _owner()
    uses_secrets = bool(credential) or _mentions_secret(url, headers, body, json_body)
    resolver = await Resolver.for_owner(owner)
    if uses_secrets and not owner:
        return ToolOutcome.failure(
            'no_credentials', 'Error: saved credentials are only available to a signed-in person',
        )

    allow_private = os.getenv('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', '').strip().lower() in ('1', 'true', 'yes')

    # The address itself may carry a placeholder (an API key in the query).
    # Its host is the part before any placeholder, so check that first.
    raw_host = _host(url)
    if PLACEHOLDER_RE.search(raw_host):
        return ToolOutcome.failure('bad_url', 'Error: a credential cannot be part of the host name')
    try:
        real_url = resolver.fill(url, raw_host)
        send_headers = {str(k): str(resolver.fill(str(v), raw_host)) for k, v in (headers or {}).items()}
        if credential:
            send_headers.update(resolver.auth_headers(str(credential).strip(), raw_host))
        send_body = resolver.fill(body, raw_host) if body is not None else None
        send_json = _fill_json(json_body, resolver, raw_host) if json_body is not None else None
    except SecretUseError as exc:
        return ToolOutcome.failure('credential_not_allowed', f'Error: {exc}', denied=True)

    secrets_map = resolver.redaction_map()
    current = real_url
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                reason = await asyncio.to_thread(
                    check_public_url, current, label='URL', allow_private=allow_private,
                    allow_env='COGNITRIX_WEBFETCH_ALLOW_PRIVATE',
                )
                if reason:
                    return ToolOutcome.failure('blocked_url', redact(f'Error: {reason}', secrets_map))
                response = await client.request(
                    method, current, headers=send_headers, content=send_body, json=send_json,
                )
                location = response.headers.get('location')
                if not (response.is_redirect and location):
                    break
                nxt = urljoin(current, location)
                if resolver.used and _host(nxt) != _host(current):
                    # Never carry a credential to another host.
                    return ToolOutcome.success(redact(
                        f'HTTP {response.status_code} redirect to {nxt}\n'
                        'Not followed: the request carried a saved credential and the redirect goes to another host.',
                        secrets_map,
                    ))
                if response.status_code in (301, 302, 303) and method not in ('GET', 'HEAD'):
                    method, send_body, send_json = 'GET', None, None
                current = nxt
            else:
                return ToolOutcome.failure('too_many_redirects', 'Error: too many redirects')
    except httpx.HTTPError as exc:
        return ToolOutcome.failure('http_error', redact(f'Error: request failed: {exc}', secrets_map))
    finally:
        await resolver.mark_used()

    shown = [f'{name}: {response.headers[name]}' for name in _SHOWN_HEADERS if name in response.headers]
    text = response.text if method != 'HEAD' else ''
    if len(text) > max_length:
        text = text[:max_length] + '\n... (truncated)'
    lines = [f'HTTP {response.status_code} {response.reason_phrase}', f'URL: {url}']
    if resolver.used:
        lines.append(f"Signed in with: {', '.join(sorted(resolver.used))}")
    lines.extend(shown)
    lines.extend(['', text])
    return redact('\n'.join(lines), secrets_map)


@tool(category='web')
async def list_credentials():
    """List the saved credentials you can use, without their values.

    Each entry has a name, a kind (token, basic or oauth), a description and
    the hosts it may be sent to. Use a name with Http Request (`credential`, or
    {{secret:NAME}} inside the request). You can't read the values themselves.

    Returns:
        str: One line per saved credential, or a note that there are none.
    """
    rows = await load_rows(_owner())
    if not rows:
        return 'No saved credentials. The person can add them in Settings > Saved sign-ins.'
    lines = []
    for row in sorted(rows, key=lambda r: r.name):
        info = describe(row)
        hosts = ', '.join(info['allowed_hosts']) or 'no hosts'
        desc = f" - {info['description']}" if info['description'] else ''
        lines.append(f"{info['name']} ({info['kind']}, for {hosts}){desc}")
    return '\n'.join(lines)
