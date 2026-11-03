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
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_GOOGLE_DRIVE_READ = (
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file',
)
# Asked for with every Google app so the card can show which account it uses.
_GOOGLE_PROFILE = ('openid', 'email')


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


def _google(app_id: str, name: str, description: str, host: str, scopes: tuple[str, ...]) -> CatalogApp:
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
    )),
    _google('google-calendar', 'Google Calendar', 'Your calendars, events and free time.', 'calendarmcp.googleapis.com', (
        'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
        'https://www.googleapis.com/auth/calendar.events.freebusy',
        'https://www.googleapis.com/auth/calendar.events.readonly',
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

PROVIDER_LABEL = {'google': 'Google', 'github': 'GitHub'}


def get(app_id: str | None) -> CatalogApp | None:
    return _BY_ID.get(str(app_id or ''))


def provider_credentials(provider: str | None) -> tuple[str, str] | None:
    """The OAuth client this server has for ``provider``, if any."""
    if not provider:
        return None
    prefix = provider.upper()
    client_id = os.getenv(f'{prefix}_CLIENT_ID', '').strip()
    secret = os.getenv(f'{prefix}_CLIENT_SECRET', '').strip()
    return (client_id, secret) if client_id and secret else None


def available(app: CatalogApp) -> bool:
    return app.auth != 'provider' or provider_credentials(app.provider) is not None


def setup_hint(app: CatalogApp) -> str:
    """What an admin has to do before people can connect this app."""
    if app.auth != 'provider' or available(app):
        return app.setup
    prefix = (app.provider or '').upper()
    hint = (
        f'Set {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET for this server '
        f'(the same {PROVIDER_LABEL.get(app.provider or "", "")} OAuth app used for sign-in).'
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
    }
