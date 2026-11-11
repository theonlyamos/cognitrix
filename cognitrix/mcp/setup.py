"""Guided setup for the OAuth apps behind Google, GitHub and Slack connections.

Those three don't let Cognitrix register itself, so an admin creates an OAuth
app with each provider once. This module is what Settings > Connected apps >
Set up shows: the steps for each provider with everything to paste already
filled in (redirect address, scopes, a Slack app manifest), a place to save
the client ID and secret without touching the server's environment, and
checks that say what is still wrong.

``check_provider`` proves the client ID and secret without a person signing
in: it trades a made-up code at the provider's token address and reads the
error. A wrong client is refused as such; a right one is told the code is bad.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from cognitrix.mcp import catalog, native, oauth

HTTP_TIMEOUT = 15
_PROBE_CODE = 'cognitrix-setup-check'

GOOGLE_APIS = (
    'gmail.googleapis.com', 'calendar-json.googleapis.com', 'drive.googleapis.com',
    'docs.googleapis.com', 'sheets.googleapis.com', 'slides.googleapis.com',
    'gmailmcp.googleapis.com', 'calendarmcp.googleapis.com', 'drivemcp.googleapis.com',
    'docsmcp.googleapis.com', 'sheetsmcp.googleapis.com', 'slidesmcp.googleapis.com',
)

# What each provider answers for a wrong client, and for a right client with a bad code.
_BAD_CLIENT = {
    'google': {'invalid_client', 'unauthorized_client', 'deleted_client'},
    'github': {'incorrect_client_credentials', 'not_found'},
    'slack': {'invalid_client_id', 'bad_client_secret', 'invalid_client'},
}
_GOOD_CLIENT = {
    'google': {'invalid_grant'},
    'github': {'bad_verification_code'},
    'slack': {'invalid_code', 'code_already_used'},
}
_BAD_REDIRECT = {'redirect_uri_mismatch', 'bad_redirect_uri'}


def _apps(provider: str) -> list[catalog.CatalogApp]:
    return [a for a in catalog.CATALOG if a.provider == provider]


def all_scopes(provider: str) -> list[str]:
    """Every scope the provider's apps can ask for, options included."""
    scopes: list[str] = []
    for app in _apps(provider):
        for scope in (*app.scopes, *(s for o in app.options for s in o.scopes)):
            if scope not in scopes:
                scopes.append(scope)
    return scopes


def slack_manifest(redirect_uri: str) -> str:
    return json.dumps({
        'display_information': {
            'name': 'Cognitrix',
            'description': 'Lets my Cognitrix agents search, read and send in Slack as me.',
        },
        'oauth_config': {
            'redirect_urls': [redirect_uri],
            'scopes': {'user': list(catalog.SLACK_SCOPES)},
        },
        'settings': {
            'org_deploy_enabled': False,
            'socket_mode_enabled': False,
            'token_rotation_enabled': False,
        },
    }, indent=2)


def _step(title: str, body: str, *, link: tuple[str, str] | None = None,
          copy: tuple[str, str] | None = None) -> dict[str, Any]:
    step: dict[str, Any] = {'title': title, 'body': body}
    if link:
        step['link'] = {'label': link[0], 'url': link[1]}
    if copy:
        step['copy'] = {'label': copy[0], 'text': copy[1]}
    return step


def _steps(provider: str, redirect_uri: str, base: str) -> list[dict[str, Any]]:
    if provider == 'google':
        return [
            _step('Pick a Google Cloud project',
                  'Use an existing project or create one. The Gmail, Calendar, Drive, Docs, Sheets and Slides '
                  'servers are in Google\'s Workspace Developer Preview, so the account that owns the project '
                  'has to join that program first.',
                  link=('Open Google Cloud console', 'https://console.cloud.google.com/projectcreate')),
            _step('Turn on the APIs',
                  'Each app needs its normal API and its MCP API. Run this in Cloud Shell, or turn each one on '
                  'under APIs & Services › Library.',
                  copy=('Command', 'gcloud services enable ' + ' '.join(GOOGLE_APIS))),
            _step('Set up the consent screen',
                  'Under Google Auth Platform, fill in Branding. For Audience pick Internal, or External and '
                  'add every person who will connect as a test user.',
                  link=('Open Google Auth Platform', 'https://console.cloud.google.com/auth/overview')),
            _step('Add the scopes',
                  'Under Data Access › Add or remove scopes, paste these into "Manually add scopes".',
                  copy=('Scopes', '\n'.join(all_scopes('google')))),
            _step('Create the client',
                  'Under Clients › Create client choose "Web application", add the redirect address below as '
                  'an authorized redirect URI, and tick "Use this client for an AI-powered agent".',
                  copy=('Redirect address', redirect_uri)),
            _step('Save the client ID and secret', 'Paste them below, then press Check.'),
        ]
    if provider == 'github':
        return [
            _step('Create an OAuth app',
                  'On GitHub open Settings › Developer settings › OAuth Apps › New OAuth App. Any name works. '
                  'Use the homepage and callback addresses below.',
                  link=('Open GitHub OAuth apps', 'https://github.com/settings/developers'),
                  copy=('Authorization callback URL', redirect_uri)),
            _step('Homepage URL', 'GitHub asks for one; use the address of this server.',
                  copy=('Homepage URL', base)),
            _step('Save the client ID and secret',
                  'Press "Generate a new client secret" on the app\'s page, paste both below, then press Check. '
                  'People can also connect GitHub with a personal access token, which needs none of this.'),
        ]
    return [
        _step('Create a Slack app from this manifest',
              'On api.slack.com/apps choose Create New App › From a manifest, pick your workspace and paste '
              'this. It sets the redirect address and the permissions. Keep the app internal to your '
              'workspace: Slack only lets internal apps (or ones in its Marketplace) use its MCP server.',
              link=('Open Slack apps', 'https://api.slack.com/apps?new_app=1'),
              copy=('App manifest', slack_manifest(redirect_uri))),
        _step('Save the client ID and secret',
              'They are on the app\'s Basic Information page under App Credentials. Paste both below, then '
              'press Check. If your workspace needs admin approval for apps, ask an admin to approve it.'),
    ]


def _redirect_problem(provider: str, redirect_uri: str) -> str | None:
    parsed = urlparse(redirect_uri)
    host = (parsed.hostname or '').lower()
    local = host in ('localhost', '127.0.0.1', '::1')
    if parsed.scheme == 'https':
        return None
    if provider == 'slack':
        return 'Slack only accepts https redirect addresses. Put this server behind https and set COGNITRIX_PUBLIC_URL.'
    if provider == 'google' and not local:
        return 'Google only accepts http for localhost. Put this server behind https and set COGNITRIX_PUBLIC_URL.'
    return None


def describe(provider: str, base: str) -> dict[str, Any]:
    """What the setup page shows for one provider. Never the secret."""
    redirect_uri = oauth.provider_callback_url(base, provider)
    source = catalog.credentials_source(provider)
    creds = catalog.provider_credentials(provider)
    prefix = provider.upper()
    return {
        'id': provider,
        'label': catalog.PROVIDER_LABEL[provider],
        'source': source,
        'can_edit': source != 'env',
        'client_id': creds[0] if creds else '',
        'env_vars': [f'{prefix}_CLIENT_ID', f'{prefix}_CLIENT_SECRET'],
        'redirect_uri': redirect_uri,
        'redirect_problem': _redirect_problem(provider, redirect_uri),
        'apps': [a.name for a in _apps(provider)],
        'steps': _steps(provider, redirect_uri, base),
    }


def _check(label: str, ok: bool | None, detail: str = '') -> dict[str, Any]:
    """ok None means "couldn't tell"."""
    return {'label': label, 'ok': ok, 'detail': detail}


async def check_provider(provider: str, base: str) -> dict[str, Any]:
    """Check a provider's saved OAuth app. ``ok`` is true when nothing is known to be wrong."""
    label = catalog.PROVIDER_LABEL[provider]
    redirect_uri = oauth.provider_callback_url(base, provider)
    checks: list[dict[str, Any]] = []
    creds = catalog.provider_credentials(provider)
    checks.append(_check('Client ID and secret are saved', creds is not None,
                         '' if creds else 'Paste both and press Save first.'))
    problem = _redirect_problem(provider, redirect_uri)
    checks.append(_check('The redirect address can be used', problem is None, problem or redirect_uri))
    if creds is not None:
        form = {
            'grant_type': 'authorization_code', 'code': _PROBE_CODE, 'redirect_uri': redirect_uri,
            'client_id': creds[0], 'client_secret': creds[1],
        }
        error = ''
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.post(oauth.PROVIDER_ENDPOINTS[provider]['token'], data=form,
                                             headers={'Accept': 'application/json'})
            data = response.json() if response.content else {}
            error = str(data.get('error') or '') if isinstance(data, dict) else ''
        except (httpx.HTTPError, ValueError):
            checks.append(_check(f'{label} accepts the client', None, f"Couldn't reach {label}. Try again in a moment."))
        else:
            if error in _BAD_CLIENT[provider]:
                checks.append(_check(f'{label} accepts the client', False,
                                     f"{label} doesn't recognise this client ID and secret. Copy them again."))
            elif error in _BAD_REDIRECT:
                checks.append(_check(f'{label} accepts the client', True))
                checks.append(_check('The redirect address is registered', False,
                                     f'Add {redirect_uri} to the app\'s redirect addresses.'))
            elif error in _GOOD_CLIENT[provider]:
                checks.append(_check(f'{label} accepts the client', True))
            else:
                checks.append(_check(f'{label} accepts the client', None,
                                     f'{label} gave an answer this check doesn\'t know ({error or response.status_code}).'))
    return {'ok': all(c['ok'] is not False for c in checks) and creds is not None, 'checks': checks}


async def check_connection(row: Any) -> dict[str, Any]:
    """Try a person's own connection: sign-in, the app's server, and its options."""
    from cognitrix.mcp import apps, composio, registry
    from cognitrix.mcp.client import get_dynamic_client

    name = str(row.description or row.name) if composio.is_app(row) else oauth.app_name(row)
    app = catalog.get(getattr(row, 'catalog_id', None))
    config = registry.config_from_row(row)
    client = await get_dynamic_client()
    checks: list[dict[str, Any]] = []

    if client.is_connected(config.key):
        await client.disconnect_from_server(config.key)
    problem = await registry.prepare(client, config)
    if problem and problem.startswith(registry.SIGN_IN_NEEDED):
        checks.append(_check('Signed in', False, f'Press Connect next to {name} to sign in again.'))
        return {'ok': False, 'checks': checks, 'tools': 0}
    checks.append(_check('Signed in', True, str(row.account or '')))
    if problem:
        hint = problem
        if app and app.provider == 'google':
            hint = (f"Couldn't open {name}'s server. Usually the {name} MCP API isn't turned on in the Google "
                    'Cloud project yet, or the account is not in the Workspace Developer Preview.')
        elif app and app.provider == 'slack':
            hint = ("Couldn't open Slack's server. Check that the Slack app is internal to your workspace "
                    'and approved by a workspace admin.')
        checks.append(_check(f"{name}'s server answers", False, hint))
        return {'ok': False, 'checks': checks, 'tools': 0}
    tools = await apps.list_tools(client, config)
    checks.append(_check(f"{name}'s server answers", bool(tools),
                         f'{len(tools)} tools' if tools else 'It connected but offers no tools.'))
    for chosen in catalog.chosen_options(app, row):
        granted = native.option_granted(row, chosen)
        checks.append(_check(chosen.label, granted,
                             '' if granted else f'Press Reconnect so {name} can grant this permission.'))
    return {'ok': all(c['ok'] is not False for c in checks), 'checks': checks, 'tools': len(tools)}
