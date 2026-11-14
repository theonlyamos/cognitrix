"""What's new: release notes shown once after the app is updated.

Each release ships its notes as ``cognitrix/release_notes/<version>.md``
(the same text as the GitHub release), so the window works offline in the
desktop app, pip installs and Docker images alike. Every user keeps the last
version whose notes they saw (``users.whats_new_seen``):

* ``GET /whats-new`` says whether to open the window and returns the notes of
  every version newer than the one last seen, newest first.
* ``POST /whats-new/seen`` records the running version as seen.
* ``GET /whats-new/notes`` returns recent notes for the "What's new" menu
  item, whether or not they were seen.

Brand-new accounts never see the window: their first run is the assistant
setup dialog, so the running version is recorded as seen there and then.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from cognitrix.agents import access as agent_access
from cognitrix.agents import starter_team
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.config import VERSION
from cognitrix.models import User

logger = logging.getLogger('cognitrix.log')

whats_new_api = APIRouter(prefix='/whats-new', dependencies=[Depends(jwt_only)])

NOTES_DIR = Path(__file__).resolve().parent.parent.parent / 'release_notes'
RELEASES_URL = 'https://github.com/theonlyamos/cognitrix/releases'
# Someone who skipped many versions sees the latest few, not the whole history.
MAX_RELEASES = 5


def parse_version(raw: str | None) -> tuple[int, ...] | None:
    """``'0.5.10'`` -> ``(0, 5, 10)``. Pre-release and build tags are ignored,
    so ``0.5.6.dev1`` and ``0.5.6+local`` count as 0.5.6."""
    if not raw:
        return None
    match = re.match(r'\s*v?(\d+(?:\.\d+)*)', str(raw))
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split('.')]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _clean(markdown: str) -> str:
    """Drop what only makes sense on GitHub: the "# Cognitrix vX" title (the
    window has its own), the "Covers PRs ..." line and a leading "Highlights"
    heading."""
    lines = markdown.strip().splitlines()
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    lines = [ln for ln in lines if not re.match(r'\s*Covers PRs? #', ln)]
    text = '\n'.join(lines).strip()
    # The opening "## Highlights" heading adds nothing inside the window.
    return re.sub(r'\A## Highlights\s*\n', '', text).strip()


@lru_cache(maxsize=1)
def all_releases() -> tuple[dict[str, Any], ...]:
    """Every bundled release, newest first."""
    found = []
    try:
        files = list(NOTES_DIR.glob('*.md'))
    except OSError:
        files = []
    for path in files:
        parsed = parse_version(path.stem)
        if parsed is None:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            logger.warning('Could not read release notes %s', path)
            continue
        found.append({'version': path.stem, 'key': parsed, 'notes': _clean(text)})
    found.sort(key=lambda r: r['key'], reverse=True)
    return tuple(found)


def releases_between(seen: str | None, current: str) -> list[dict[str, Any]]:
    """Releases after ``seen`` up to and including ``current``, newest first.
    With nothing seen yet, just the current release."""
    top = parse_version(current)
    low = parse_version(seen)
    out = []
    for rel in all_releases():
        if top is not None and rel['key'] > top:
            continue
        if low is not None and rel['key'] <= low:
            continue
        out.append({'version': rel['version'], 'notes': rel['notes'], 'url': f"{RELEASES_URL}/tag/v{rel['version']}"})
        if low is None or len(out) >= MAX_RELEASES:
            break
    return out


async def _mark_seen(user: User, version: str) -> None:
    await User.update_one({'id': user.id}, {'whats_new_seen': version})
    try:
        user.whats_new_seen = version
    except Exception:
        pass


async def _starter_offer(user: User) -> list[dict[str, Any]]:
    """Starter specialists this person lacks, when they have an assistant the
    team could run under. Empty when there's nothing to offer."""
    try:
        user_key = agent_access.owner_key(user)
        missing = {s.key for s in await starter_team.missing_specialists(user_key)}
        if not missing:
            return []
        from cognitrix.api.routes.onboarding import _owned_agents

        known = {s.name.lower() for s in starter_team.SPECIALISTS}
        owned = await _owned_agents(user_key)
        if not any(str(a.name).strip().lower() not in known for a in owned):
            return []
        return [item for item in starter_team.catalog() if item['key'] in missing]
    except Exception:
        logger.exception('Could not work out the starter team offer')
        return []


@whats_new_api.get('')
async def whats_new(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    from cognitrix.api.routes.onboarding import _needs_setup

    user = ctx.user
    seen = getattr(user, 'whats_new_seen', None)
    current = parse_version(VERSION)
    base = {'version': VERSION, 'show': False, 'releases': [], 'starter_team': [], 'releases_url': RELEASES_URL}
    if current is None:
        return base
    if seen is None and await _needs_setup(user):
        # A new account: the setup dialog is its welcome, not last release's notes.
        await _mark_seen(user, VERSION)
        return base
    seen_key = parse_version(seen)
    if seen_key is not None and seen_key >= current:
        return base
    releases = releases_between(seen, VERSION)
    if not releases:
        # A version with no bundled notes (a dev build): nothing to show, and
        # nothing recorded, so the next real release still shows.
        return base
    return {**base, 'show': True, 'releases': releases, 'starter_team': await _starter_offer(user)}


@whats_new_api.post('/seen')
async def mark_seen(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    await _mark_seen(ctx.user, VERSION)
    return {'seen': VERSION}


@whats_new_api.get('/notes')
async def recent_notes(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    top = parse_version(VERSION)
    releases = [
        {'version': r['version'], 'notes': r['notes'], 'url': f"{RELEASES_URL}/tag/v{r['version']}"}
        for r in all_releases()
        if top is None or r['key'] <= top
    ][:MAX_RELEASES]
    return {
        'version': VERSION,
        'releases': releases,
        'starter_team': await _starter_offer(ctx.user),
        'releases_url': RELEASES_URL,
    }
