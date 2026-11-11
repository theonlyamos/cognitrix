"""The connected apps people can pick in Settings > Connected apps.

Every entry is a hosted (remote) MCP server. Signing in works one of two ways:

- ``provider``: the app signs in through an OAuth app this server already has
  for "Sign in with Google / GitHub" (``GOOGLE_CLIENT_ID`` /
  ``GOOGLE_CLIENT_SECRET``, ``GITHUB_CLIENT_ID`` / ``GITHUB_CLIENT_SECRET``),
  using the same callback address. GitHub's and Google's MCP servers don't
  let clients register themselves, so this is the only way to reach them.
- ``discover``: the server describes its own sign-in (MCP authorization:
  protected-resource metadata, then a client ID metadata document or dynamic
  client registration). Nothing needs setting up.

Google's Workspace MCP servers are in Google's developer preview. Each one
needs its API turned on in the Google Cloud project that owns the client ID
(APIs & Services > Library, search "<app> MCP").

Slack's MCP server only accepts a Slack app you registered yourself
(``SLACK_CLIENT_ID`` / ``SLACK_CLIENT_SECRET``); it has no sign-in button of
its own in Cognitrix, so its callback is still the provider one.

The client ID and secret of each provider come from the environment, or from
Settings > Connected apps > Set up (``cognitrix/mcp/setup.py``), which saves
them sealed next to the provider keys. The environment wins.

``options`` are extra permissions a person can turn on for their own
connection, for example "Send email" on Gmail. An option can add OAuth scopes
(asked for at the next sign-in) and built-in tools (``cognitrix/mcp/native.py``)
that work next to the server's own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_GOOGLE_DRIVE_READ = (
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file',
)
# User-token scopes for Slack's MCP server (docs.slack.dev/ai/slack-mcp-server).
SLACK_SCOPES = (
    'search:read.public', 'search:read.private', 'search:read.mpim', 'search:read.im',
    'search:read.files', 'search:read.users',
    'channels:history', 'groups:history', 'mpim:history', 'im:history',
    'channels:read', 'groups:read', 'im:read', 'mpim:read',
    'users:read', 'users:read.email',
    'chat:write', 'reactions:read', 'reactions:write',
    'canvases:read', 'canvases:write', 'files:read',
)
# Asked for with every Google app so the card can show which account it uses.
_GOOGLE_PROFILE = ('openid', 'email')


@dataclass(frozen=True)
class AppOption:
    """An extra permission a person can turn on for their connection."""

    id: str
    label: str
    description: str
    # Scopes the option needs on top of the app's own. Empty when the app's
    # scopes already cover it.
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogApp:
    id: str
    name: str
    description: str
    url: str
    auth: str  # 'provider' | 'discover'
    provider: str | None = None  # 'google' | 'github' for auth == 'provider'
    scopes: tuple[str, ...] = ()
    category: str = ''
    # A personal access token works instead of signing in (GitHub).
    token_help: str | None = None
    setup: str = ''
    extra_params: dict[str, str] = field(default_factory=dict)
    options: tuple[AppOption, ...] = ()


def _google(app_id: str, name: str, description: str, host: str, scopes: tuple[str, ...],
            options: tuple[AppOption, ...] = ()) -> CatalogApp:
    return CatalogApp(
        id=app_id,
        name=name,
        description=description,
        url=f'https://{host}/mcp/v1',
        auth='provider',
        provider='google',
        scopes=_GOOGLE_PROFILE + scopes,
        category='Google',
        setup=f'Turn on the {name} MCP API in the Google Cloud project that owns GOOGLE_CLIENT_ID.',
        # A refresh token only comes back for offline access, and again on
        # later sign-ins only with prompt=consent.
        extra_params={'access_type': 'offline', 'prompt': 'consent', 'include_granted_scopes': 'true'},
        options=options,
    )


CATALOG: tuple[CatalogApp, ...] = (
    CatalogApp(
        id='github',
        name='GitHub',
        description='Repositories, issues, pull requests and code search.',
        url='https://api.githubcopilot.com/mcp/',
        auth='provider',
        provider='github',
        scopes=('repo', 'read:org', 'read:user'),
        category='Developer',
        token_help='Or paste a personal access token from github.com/settings/tokens.',
    ),
    _google('gmail', 'Gmail', 'Search and read mail, and write drafts.', 'gmailmcp.googleapis.com', (
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.compose',
    ), options=(
        # gmail.compose already allows sending, so this needs no new sign-in.
        AppOption('send', 'Send email', 'Agents can send mail and saved drafts. Each send asks you first.'),
    )),
    _google('google-calendar', 'Google Calendar', 'Your calendars, events and free time.', 'calendarmcp.googleapis.com', (
        'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
        'https://www.googleapis.com/auth/calendar.events.freebusy',
        'https://www.googleapis.com/auth/calendar.events.readonly',
    ), options=(
        AppOption('edit', 'Create and change events',
                  'Agents can add, change and delete events. Each change asks you first.',
                  scopes=('https://www.googleapis.com/auth/calendar.events',)),
    )),
    _google('google-drive', 'Google Drive', 'Find, read and create files in Drive.', 'drivemcp.googleapis.com',
            _GOOGLE_DRIVE_READ),
    _google('google-docs', 'Google Docs', 'Read and edit documents.', 'docsmcp.googleapis.com', _GOOGLE_DRIVE_READ + (
        'https://www.googleapis.com/auth/documents.readonly',
        'https://www.googleapis.com/auth/documents',
    )),
    _google('google-sheets', 'Google Sheets', 'Read and edit spreadsheets.', 'sheetsmcp.googleapis.com', _GOOGLE_DRIVE_READ + (
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/spreadsheets',
    )),
    _google('google-slides', 'Google Slides', 'Read and edit presentations.', 'slidesmcp.googleapis.com', _GOOGLE_DRIVE_READ + (
        'https://www.googleapis.com/auth/presentations.readonly',
        'https://www.googleapis.com/auth/presentations',
    )),
    CatalogApp(
        id='slack', name='Slack', description='Search and read messages, and send them.',
        url='https://mcp.slack.com/mcp', auth='provider', provider='slack', scopes=SLACK_SCOPES,
        category='Communication',
        setup='Slack only lets a Slack app you created yourself (an internal app) use its MCP server.',
    ),
    CatalogApp(
        id='notion', name='Notion', description='Search, read and write pages and databases.',
        url='https://mcp.notion.com/mcp', auth='discover', category='Productivity',
    ),
    CatalogApp(
        id='linear', name='Linear', description='Issues, projects and cycles.',
        url='https://mcp.linear.app/mcp', auth='discover', category='Developer',
    ),
    CatalogApp(
        id='sentry', name='Sentry', description='Errors, issues and releases.',
        url='https://mcp.sentry.dev/mcp', auth='discover', category='Developer',
    ),
)

_BY_ID = {app.id: app for app in CATALOG}

PROVIDER_LABEL = {'google': 'Google', 'github': 'GitHub', 'slack': 'Slack'}
PROVIDERS = tuple(PROVIDER_LABEL)


def get(app_id: str | None) -> CatalogApp | None:
    return _BY_ID.get(str(app_id or ''))


def _saved_name(provider: str, part: str) -> str:
    return f'oauth_{provider}_{part}'


def _env_credentials(provider: str) -> tuple[str, str] | None:
    prefix = provider.upper()
    client_id = os.getenv(f'{prefix}_CLIENT_ID', '').strip()
    secret = os.getenv(f'{prefix}_CLIENT_SECRET', '').strip()
    return (client_id, secret) if client_id and secret else None


def _saved_credentials(provider: str) -> tuple[str, str] | None:
    from cognitrix.providers.saved_keys import get_saved_key

    client_id = get_saved_key(_saved_name(provider, 'client_id'))
    secret = get_saved_key(_saved_name(provider, 'client_secret'))
    return (client_id, secret) if client_id and secret else None


def credentials_source(provider: str | None) -> str:
    """Where a provider's OAuth client comes from: 'env', 'saved' or 'none'."""
    if not provider or provider not in PROVIDER_LABEL:
        return 'none'
    if _env_credentials(provider):
        return 'env'
    return 'saved' if _saved_credentials(provider) else 'none'


def provider_credentials(provider: str | None) -> tuple[str, str] | None:
    """The OAuth client this server has for ``provider``, if any."""
    if not provider or provider not in PROVIDER_LABEL:
        return None
    return _env_credentials(provider) or _saved_credentials(provider)


def save_provider_credentials(provider: str, client_id: str, client_secret: str) -> None:
    """Save (or, with empty values, forget) a provider's OAuth client from Settings."""
    from cognitrix.providers.saved_keys import set_saved_key

    if provider not in PROVIDER_LABEL:
        raise ValueError('Unknown provider')
    set_saved_key(_saved_name(provider, 'client_id'), client_id)
    set_saved_key(_saved_name(provider, 'client_secret'), client_secret)


def option(app: CatalogApp | None, option_id: str) -> AppOption | None:
    return next((o for o in (app.options if app else ()) if o.id == option_id), None)


def chosen_options(app: CatalogApp | None, row: Any) -> list[AppOption]:
    """The options this person turned on for their connection."""
    wanted = {str(o) for o in (getattr(row, 'options', None) or [])}
    return [o for o in (app.options if app else ()) if o.id in wanted]


def scopes_for(app: CatalogApp, row: Any) -> tuple[str, ...]:
    """What to ask for at sign-in: the app's scopes plus its chosen options'."""
    scopes = list(app.scopes)
    for chosen in chosen_options(app, row):
        scopes.extend(s for s in chosen.scopes if s not in scopes)
    return tuple(scopes)


def available(app: CatalogApp) -> bool:
    return app.auth != 'provider' or provider_credentials(app.provider) is not None


def setup_hint(app: CatalogApp) -> str:
    """What an admin has to do before people can connect this app."""
    if app.auth != 'provider' or available(app):
        return app.setup
    prefix = (app.provider or '').upper()
    label = PROVIDER_LABEL.get(app.provider or '', '')
    hint = (
        f'An admin has to add a {label} OAuth app first: open Set up on this page, '
        f'or set {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET for this server.'
    )
    return f'{hint} {app.setup}'.strip()


def public(app: CatalogApp) -> dict[str, Any]:
    return {
        'id': app.id,
        'name': app.name,
        'description': app.description,
        'category': app.category,
        'auth': app.auth,
        'provider': app.provider,
        'available': available(app),
        'setup': setup_hint(app),
        'token_help': app.token_help,
        'options': [
            {'id': o.id, 'label': o.label, 'description': o.description, 'needs_sign_in': bool(o.scopes)}
            for o in app.options
        ],
    }
