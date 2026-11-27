"""Who a visitor from the tailnet is.

Tailscale Serve adds the visitor's login, name and picture to each request.
They are read here, and only for requests that came through the tailnet door
(``doors.DoorGuard`` strips them everywhere else). A tagged device belongs to
no person and has none; neither does anything that came through the public
address.

Two things use it:

- **Apps shared with the tailnet** (``hosted_apps``): a visitor without a
  Cognitrix account opens an App that was shared with their login, or with
  everyone on the tailnet.
- **Signing in from the tailnet**: someone links the Tailscale login they are
  seen as to their account (Settings > Sign-ins). After that, opening
  Cognitrix from their tailnet signs them in. Nothing is ever linked by
  matching email addresses: the login is only tied to the account that was
  signed in when its owner linked it. Off for the server until an admin turns
  it on (Settings > Tailscale).
"""
from __future__ import annotations

from dataclasses import dataclass
from email.header import decode_header, make_header
from typing import Any

from cognitrix.app_settings import get_bool
from cognitrix.tailscale import doors

GUEST_PREFIX = 'ts:'
MAX_LOGIN = 254


@dataclass(frozen=True)
class Visitor:
    login: str          # lower case
    name: str = ''
    picture: str = ''

    @property
    def key(self) -> str:
        """How this visitor is named where a Cognitrix account id would be."""
        return guest_key(self.login)


class IdentityError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def clean_login(login: Any) -> str:
    """A Tailscale login in the one form we compare: '' when it isn't one."""
    text = str(login or '').strip().lower()
    if not text or len(text) > MAX_LOGIN or any(c.isspace() or ord(c) < 33 or c in '<>"\'\\' for c in text):
        return ''
    return text


def guest_key(login: str) -> str:
    return GUEST_PREFIX + clean_login(login)


def is_guest(key: Any) -> bool:
    return str(key or '').startswith(GUEST_PREFIX)


def login_of(key: Any) -> str:
    return str(key)[len(GUEST_PREFIX):] if is_guest(key) else ''


def _text(value: str, limit: int) -> str:
    # Tailscale sends names with other than plain letters in RFC 2047 form.
    try:
        value = str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        pass
    return ''.join(c for c in value if c.isprintable()).strip()[:limit]


def from_another_site(request: Any) -> bool:
    """True when a browser says another site's page made this request.

    Who a tailnet visitor is travels with every request their browser makes
    to the private address, whichever page asked. So anything that acts on it
    for Cognitrix's own pages (signing in, linking, handing out a way in to an
    App) refuses requests another site's page made. Programs that are not
    browsers (a PC's connector) send neither header and pass."""
    try:
        headers = request.headers
        site = str(headers.get('sec-fetch-site') or '').strip().lower()
        if site and site not in ('same-origin', 'none'):
            return True
        origin = str(headers.get('origin') or '').strip().lower().rstrip('/')
        if not origin:
            return False
        host = str(headers.get('host') or '').strip().lower()
        return not host or origin not in (f'https://{host}', f'http://{host}')
    except Exception:  # noqa: BLE001
        return True


def visitor(request: Any, *, own_page: bool = False) -> Visitor | None:
    """The person Tailscale says is asking, or None. ``own_page``: also None
    when another site's page made the request (see ``from_another_site``)."""
    if request is None or doors.door_of(request) != doors.TAILNET:
        return None
    if own_page and from_another_site(request):
        return None
    try:
        headers = request.headers
        login = clean_login(headers.get('tailscale-user-login'))
        if not login:
            return None
        picture = str(headers.get('tailscale-user-profile-pic') or '').strip()
        return Visitor(login, _text(str(headers.get('tailscale-user-name') or ''), 120),
                       picture[:500] if picture.startswith('https://') else '')
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Signing in from the tailnet
# ---------------------------------------------------------------------------

def signin_on() -> bool:
    from cognitrix import tailscale

    return tailscale.serve_wanted() and get_bool('COGNITRIX_TAILSCALE_SIGNIN', False)


async def _links(login: str) -> list[Any]:
    from cognitrix.models.sign_in import TailscaleLink

    login = clean_login(login)
    return list(await TailscaleLink.find({'login': login}) or []) if login else []


async def link_of_login(login: str):
    """The one account a Tailscale login is linked to. Never a guess: if two
    rows ever name the same login, it is linked to nobody."""
    rows = await _links(login)
    return rows[0] if len(rows) == 1 else None


async def link_of_user(user: Any):
    from cognitrix.models.sign_in import TailscaleLink

    return await TailscaleLink.find_one({'user_id': str(user.id)})


async def view(request: Any, user: Any = None) -> dict[str, Any]:
    """For the Login page and Settings > Sign-ins."""
    seen = visitor(request, own_page=True)
    on = signin_on()
    out: dict[str, Any] = {
        'enabled': on,
        'login': seen.login if seen else '', 'name': seen.name if seen else '',
        'picture': seen.picture if seen else '',
        # The Login page offers "Continue as ..." only when it would work.
        'can_sign_in': False,
    }
    if seen and on:
        row = await link_of_login(seen.login)
        out['can_sign_in'] = row is not None
    if user is not None:
        mine = await link_of_user(user)
        out['linked'] = mine.login if mine else ''
        out['linked_at'] = mine.created_at if mine else ''
    return out


async def link(request: Any, user: Any) -> dict[str, Any]:
    """Tie the Tailscale login this request is seen as to the signed-in account."""
    from cognitrix import audit, signins
    from cognitrix.models.sign_in import TailscaleLink

    if not signin_on():
        raise IdentityError('off', 'Signing in from the tailnet is turned off on this server.', 403)
    seen = visitor(request, own_page=True)
    if seen is None:
        raise IdentityError('not_on_tailnet', 'Open Cognitrix at its Tailscale address on this device first, '
                                              'so it can see which Tailscale login you are.')
    if any(str(row.user_id) != str(user.id) for row in await _links(seen.login)):
        raise IdentityError('taken', 'That Tailscale login is linked to another account here.', 409)
    await TailscaleLink.delete_many({'user_id': str(user.id)})
    await TailscaleLink(user_id=str(user.id), login=seen.login, name=seen.name,
                        created_at=signins.now_stamp()).save()
    audit.record('sign_in', f'Linked Tailscale login {seen.login}', user_id=str(user.id),
                 actor=str(getattr(user, 'email', '') or ''), outcome='ok')
    try:
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            str(user.id), 'Tailscale sign-in turned on',
            f'Devices signed in to Tailscale as {seen.login} can now open your Cognitrix account without a '
            'password. If this wasn\'t you, turn it off in Settings, Sign-ins, and change your password.',
            link='/settings/sign-ins')
    except Exception:  # noqa: BLE001 - the link stands; the notice is best effort
        pass
    return await view(request, user)


async def unlink(request: Any, user: Any) -> dict[str, Any]:
    from cognitrix import audit
    from cognitrix.models.sign_in import TailscaleLink

    removed = await TailscaleLink.delete_many({'user_id': str(user.id)})
    if removed:
        audit.record('sign_in', 'Unlinked their Tailscale login', user_id=str(user.id),
                     actor=str(getattr(user, 'email', '') or ''), outcome='ok')
    return await view(request, user)


async def sign_in(request: Any, *, name: str = '', kind: str = '') -> tuple[Any, str]:
    """Sign this browser in as the account linked to the visitor's Tailscale login."""
    from cognitrix import audit, signins
    from cognitrix.models import User
    from cognitrix.models.sign_in import TailscaleLink

    if not signin_on():
        raise IdentityError('off', 'Signing in from the tailnet is turned off on this server.', 403)
    seen = visitor(request, own_page=True)
    row = await link_of_login(seen.login) if seen else None
    user = await User.get(row.user_id) if row is not None else None
    if seen is None or row is None or user is None or getattr(user, 'disabled', None):
        raise IdentityError('unknown', 'No account here is linked to your Tailscale login. Sign in another way, '
                                       'then link it in Settings, Sign-ins.', 401)
    await TailscaleLink.update_one({'id': row.id}, {'last_used_at': signins.now_stamp()})
    audit.record('sign_in', f'Signed in from the tailnet ({seen.login})', user_id=str(user.id),
                 actor=str(user.email), outcome='ok')
    token = await signins.sign_in(user, request, method='tailscale', name=name, kind=kind, approved_by=seen.login)
    return user, token
