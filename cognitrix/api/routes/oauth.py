"""Sign in or sign up with Google or GitHub (OAuth 2.0 authorization code flow).

A provider is offered only when its client id and secret are set:
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET and GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET.
Register this callback with the provider:
    <base>/api/v1/auth/oauth/<provider>/callback
where <base> is COGNITRIX_PUBLIC_URL, or the address the request came in on
(http://127.0.0.1:48210 for the desktop app).

Web: the browser goes to /start, the provider sends it back to /callback, and
the callback redirects to /oauth/complete#token=... in the UI.

Desktop app: sign-in can't happen inside the app window, so the app opens
/start in the system browser with a random `poll` key, then polls
POST /oauth/poll with that key until the callback has stored the token.

Accounts: a verified provider email that matches an existing account signs
into it. A new email creates an account only when signup is open (the first
account, or COGNITRIX_ALLOW_SIGNUP=true), the same rule as password signup.
"""

import html
import os
import secrets
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from cognitrix.common.security import create_access_token
from cognitrix.config import API_VERSION
from cognitrix.models import User

oauth_api = APIRouter(prefix='/auth/oauth')

PROVIDERS: dict[str, dict[str, str]] = {
    'google': {
        'label': 'Google',
        'authorize': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token': 'https://oauth2.googleapis.com/token',
        'scope': 'openid email profile',
    },
    'github': {
        'label': 'GitHub',
        'authorize': 'https://github.com/login/oauth/authorize',
        'token': 'https://github.com/login/oauth/access_token',
        'scope': 'read:user user:email',
    },
}

FLOW_TTL = 600  # seconds a sign-in may take
_flows: dict[str, dict[str, Any]] = {}   # state -> flow
_results: dict[str, dict[str, Any]] = {}  # desktop poll key -> outcome


class OAuthError(Exception):
    """A sign-in failure with a message written for the user.

    Only `user_message` is ever shown; nothing from a provider error or a
    traceback reaches the page.
    """

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


def _credentials(provider: str) -> tuple[str, str] | None:
    prefix = provider.upper()
    client_id = os.getenv(f'{prefix}_CLIENT_ID', '').strip()
    secret = os.getenv(f'{prefix}_CLIENT_SECRET', '').strip()
    return (client_id, secret) if client_id and secret else None


def _prune() -> None:
    cutoff = time.time() - FLOW_TTL
    for store in (_flows, _results):
        for key in [k for k, v in store.items() if v['created'] < cutoff]:
            store.pop(key, None)


def _redirect_uri(request: Request, provider: str) -> str:
    base = os.getenv('COGNITRIX_PUBLIC_URL', '').strip().rstrip('/') or str(request.base_url).rstrip('/')
    return f'{base}/api/{API_VERSION}/auth/oauth/{provider}/callback'


def _provider_or_404(provider: str) -> tuple[dict[str, str], tuple[str, str]]:
    config = PROVIDERS.get(provider)
    creds = _credentials(provider) if config else None
    if not config or not creds:
        raise HTTPException(status_code=404, detail='This sign-in method is not set up')
    return config, creds


@oauth_api.get('/providers')
async def providers():
    """Which sign-in buttons to show."""
    return {name: _credentials(name) is not None for name in PROVIDERS}


@oauth_api.get('/{provider}/start')
async def start(provider: str, request: Request, poll: str | None = None):
    config, (client_id, _secret) = _provider_or_404(provider)
    if poll is not None and not (16 <= len(poll) <= 128 and poll.replace('-', '').replace('_', '').isalnum()):
        raise HTTPException(status_code=400, detail='Invalid poll key')
    _prune()
    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_uri(request, provider)
    _flows[state] = {'provider': provider, 'poll': poll, 'redirect_uri': redirect_uri, 'created': time.time()}
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': config['scope'],
        'state': state,
    }
    if provider == 'google':
        params['prompt'] = 'select_account'
    return RedirectResponse(f"{config['authorize']}?{urlencode(params)}", status_code=302)


async def _exchange_code(provider: str, code: str, redirect_uri: str) -> str:
    config, (client_id, secret) = _provider_or_404(provider)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            config['token'],
            data={
                'client_id': client_id,
                'client_secret': secret,
                'code': code,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            },
            headers={'Accept': 'application/json'},
        )
    data = response.json() if response.content else {}
    token = data.get('access_token') if isinstance(data, dict) else None
    if response.status_code >= 400 or not token:
        raise OAuthError(f"{config['label']} didn't accept the sign-in. Please try again.")
    return token


async def _profile(provider: str, access_token: str) -> tuple[str, str]:
    """(verified email, display name) from the provider."""
    headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        if provider == 'google':
            info = (await client.get('https://openidconnect.googleapis.com/v1/userinfo')).json()
            email = str(info.get('email') or '')
            if not email or info.get('email_verified') is not True:
                raise OAuthError('Your Google account has no verified email address.')
            return email, str(info.get('name') or email.split('@')[0])

        user = (await client.get('https://api.github.com/user')).json()
        emails = (await client.get('https://api.github.com/user/emails')).json()
    verified = [e for e in emails if isinstance(e, dict) and e.get('verified')] if isinstance(emails, list) else []
    chosen = next((e for e in verified if e.get('primary')), verified[0] if verified else None)
    if not chosen:
        raise OAuthError('Your GitHub account has no verified email address.')
    email = str(chosen['email'])
    return email, str(user.get('name') or user.get('login') or email.split('@')[0])


async def _signup_open() -> bool:
    allow = os.getenv('COGNITRIX_ALLOW_SIGNUP', '').strip().lower()
    if allow in ('0', 'false', 'no'):
        return False
    if allow in ('1', 'true', 'yes'):
        return True
    return not await User.find_one({})


async def sign_in_with(provider: str, email: str, name: str) -> User:
    """Find the account for a verified email, or create one if signup is open."""
    user = await User.find_one({'email': email})
    if user is None and email.lower() != email:
        user = await User.find_one({'email': email.lower()})
    if user is not None:
        return user
    if not await _signup_open():
        raise OAuthError(
            f'No Cognitrix account uses {email}, and signup is closed on this server. '
            'Sign in with your password, or ask the owner to open signup.'
        )
    # No password: this account signs in with the provider only.
    user = User(name=name, email=email, password='')
    await user.save()
    return user


def _issue_token(user: User) -> str:
    from cognitrix.common.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    return create_access_token(
        data={'sub': user.email},
        expires_delta=timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES)),
    )


def _desktop_page(title: str, body: str) -> HTMLResponse:
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0c0e;color:#e8e9eb;
font-family:system-ui,sans-serif}}main{{max-width:420px;padding:24px;text-align:center}}
h1{{font-size:20px;margin:0 0 8px}}p{{color:#9aa0a6;line-height:1.5}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p></main></body></html>"""
    return HTMLResponse(page)


@oauth_api.get('/{provider}/callback')
async def callback(provider: str, request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None):
    _prune()
    flow = _flows.pop(state or '', None)
    if flow is None or flow['provider'] != provider:
        return _finish(None, 'This sign-in link has expired. Please start again.', None)
    poll = flow['poll']
    try:
        if error or not code:
            raise OAuthError('Sign-in was cancelled.')
        access_token = await _exchange_code(provider, code, flow['redirect_uri'])
        email, name = await _profile(provider, access_token)
        user = await sign_in_with(provider, email, name)
        return _finish(user, None, poll)
    except OAuthError as exc:
        return _finish(None, exc.user_message, poll)
    except (httpx.HTTPError, ValueError, KeyError):
        label = PROVIDERS.get(provider, {}).get('label', provider)
        return _finish(None, f"Couldn't reach {label}. Please try again.", poll)


def _finish(user: User | None, error: str | None, poll: str | None):
    token = _issue_token(user) if user is not None else None
    if poll:
        _results[poll] = {'token': token, 'error': error, 'created': time.time()}
        if error:
            return _desktop_page("Couldn't sign in", error)
        return _desktop_page("You're signed in", 'You can close this tab and go back to Cognitrix.')
    if error:
        return RedirectResponse(f"/?{urlencode({'oauth_error': error})}", status_code=302)
    return RedirectResponse(f'/oauth/complete#token={token}', status_code=302)


class PollBody(BaseModel):
    poll: str


@oauth_api.post('/poll')
async def poll(body: PollBody):
    """Desktop app: the outcome of a browser sign-in, handed out once."""
    _prune()
    result = _results.pop(body.poll, None)
    if result is None:
        return {'status': 'pending'}
    if result['error']:
        return {'status': 'error', 'error': result['error']}
    return {'status': 'done', 'access_token': result['token']}
