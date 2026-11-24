"""Calls from the person's Google Calendar that an agent could join.

Every few minutes (``poll``, from the scheduler's automation tick) the
connected Google Calendar of each person who has this on is read for the
next day. Only events with a Google Meet, Teams or Zoom link are kept, and
only their title, times, organiser and link. No model is involved, and the
rest of the event (description, guests) is never stored.

Each call found is then:

- skipped, when the person said "never" for its series or a rule says so;
- approved, when they said "always" for its series, or they organise it and
  chose to have an agent join the calls they organise;
- otherwise suggested: listed under Upcoming calls on the Meetings page and,
  ``remind_minutes`` before it starts, sent to the Inbox.

An approved call is joined a minute before it starts, through the same path
as the Meetings page's Join a call (``api/routes/meetings.start_live``).
Only the person approves a call: by a tap on the Meetings page, a choice for
the whole series, or a rule they set. Agents can list upcoming calls (the
Upcoming Calls tool) but can't approve one.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from odbms import Model
from pydantic import validator

from cognitrix.app_settings import get as app_setting
from cognitrix.meetings.live import platform_of

logger = logging.getLogger('cognitrix.log')

CALENDAR_APP = 'google-calendar'
CALENDAR_API = 'https://www.googleapis.com/calendar/v3'
FMT = '%Y-%m-%d %H:%M:%S'
HTTP_TIMEOUT = 20
LOOKAHEAD_HOURS = 24
MAX_EVENTS = 50
DEFAULT_CHECK_SECONDS, MIN_CHECK_SECONDS = 600, 60
JOIN_EARLY_S = 60
"""An approved call is joined this long before it starts."""
MISSED_AFTER_S = 10 * 60
"""A suggestion nobody answered is dropped this long after the call started."""
KEEP_DAYS = 2
MAX_WORDS = 1000

SUGGESTED, APPROVED, SKIPPED, JOINED, MISSED, FAILED, CANCELLED = (
    'suggested', 'approved', 'skipped', 'joined', 'missed', 'failed', 'cancelled')
OPEN = (SUGGESTED, APPROVED)
AUTO_JOIN = ('ask', 'mine')
ACTIONS = ('send', 'skip', 'always', 'never', 'ask')

LINK_RE = re.compile(
    r'https://(?:[\w-]+\.)*(?:meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|zoom\.us)/[^\s<>"\'\]\)]+',
    re.IGNORECASE)


class CalendarError(Exception):
    """A check failed; the message is shown on the Meetings page."""


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value or '[]')
        except ValueError:
            value = []
    return [str(v) for v in (value or []) if v]


class CalendarCallSettings(Model):
    user_id: str
    enabled: bool = True
    """On for anyone who connects Google Calendar: it only suggests."""
    agent_id: str = ''
    """The agent that joins; the person's main agent when empty."""
    auto_join: str = 'ask'
    """'ask' every time, or 'mine': join calls the person organises without asking."""
    never_words: str = ''
    """Calls whose title or organiser contains one of these (one per line) are never suggested."""
    always_series: list[str] = []
    never_series: list[str] = []
    remind_minutes: int = 15
    checked_at: str = ''
    error: str = ''

    @validator('enabled', pre=True)
    def _bool(cls, value):
        return True if value is None else bool(value)

    @validator('agent_id', 'never_words', 'checked_at', 'error', pre=True)
    def _text(cls, value):
        return value or ''

    @validator('auto_join', pre=True)
    def _mode(cls, value):
        return value if value in AUTO_JOIN else 'ask'

    @validator('always_series', 'never_series', pre=True)
    def _ids(cls, value):
        return _json_list(value)

    @validator('remind_minutes', pre=True)
    def _minutes(cls, value):
        try:
            return min(120, max(1, int(value if value is not None else 15)))
        except (TypeError, ValueError):
            return 15


class CalendarCall(Model):
    user_id: str
    event_id: str
    series_id: str = ''
    title: str = ''
    starts_at: str = ''
    ends_at: str = ''
    url: str = ''
    platform: str = ''
    organizer: str = ''
    organised_by_me: bool = False
    status: str = SUGGESTED
    decided_by: str = ''
    """'you', 'series' or 'rule'."""
    alerted: bool = False
    meeting_id: str = ''
    error: str = ''

    @validator('series_id', 'title', 'starts_at', 'ends_at', 'url', 'platform', 'organizer', 'decided_by',
               'meeting_id', 'error', pre=True)
    def _text(cls, value):
        return value or ''

    @validator('organised_by_me', 'alerted', pre=True)
    def _bool(cls, value):
        return bool(value)


MODELS = (CalendarCallSettings, CalendarCall)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def check_seconds() -> int:
    try:
        return max(MIN_CHECK_SECONDS, int(app_setting('COGNITRIX_CALENDAR_CHECK_SECONDS', '')
                                          or DEFAULT_CHECK_SECONDS))
    except ValueError:
        return DEFAULT_CHECK_SECONDS


def _when(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, FMT)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ reading events

def call_link(event: dict[str, Any]) -> str:
    """The event's Meet, Teams or Zoom link, or ''."""
    candidates = [str(event.get('hangoutLink') or '')]
    for point in (event.get('conferenceData') or {}).get('entryPoints') or []:
        if point.get('entryPointType') == 'video':
            candidates.append(str(point.get('uri') or ''))
    for url in candidates:
        if url.startswith('https://') and platform_of(url) != 'other':
            return url
    for field in ('location', 'description'):
        match = LINK_RE.search(html.unescape(str(event.get(field) or '')))
        if match:
            return match.group(0).rstrip('.,;')
    return ''


def _utc(stamp: dict[str, Any] | None) -> datetime | None:
    """An event time as naive UTC; None for all-day events."""
    value = str((stamp or {}).get('dateTime') or '')
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def to_call(event: dict[str, Any]) -> dict[str, Any] | None:
    """What we keep of one calendar event, or None when it's not a call to join."""
    if event.get('status') == 'cancelled':
        return None
    for guest in event.get('attendees') or []:
        if guest.get('self') and guest.get('responseStatus') == 'declined':
            return None
    starts, ends = _utc(event.get('start')), _utc(event.get('end'))
    url = call_link(event)
    if not url or starts is None:
        return None
    organizer = event.get('organizer') or {}
    return {
        'event_id': str(event.get('id') or ''),
        'series_id': str(event.get('recurringEventId') or ''),
        'title': str(event.get('summary') or 'Call')[:160],
        'starts_at': starts.strftime(FMT),
        'ends_at': (ends or starts + timedelta(hours=1)).strftime(FMT),
        'url': url[:2000],
        'platform': platform_of(url),
        'organizer': str(organizer.get('displayName') or organizer.get('email') or '')[:160],
        'organised_by_me': bool(organizer.get('self')),
    }


async def upcoming_events(token: str, now: datetime, hours: int = LOOKAHEAD_HOURS) -> list[dict[str, Any]]:
    params = {
        'timeMin': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'timeMax': (now + timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'singleEvents': 'true', 'orderBy': 'startTime', 'maxResults': MAX_EVENTS,
        'fields': ('items(id,recurringEventId,summary,status,start,end,hangoutLink,conferenceData/entryPoints,'
                   'location,description,organizer,attendees(self,responseStatus))'),
    }
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
            response = await client.get(f'{CALENDAR_API}/calendars/primary/events', params=params)
    except httpx.HTTPError:
        raise CalendarError("Couldn't reach Google Calendar.")
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.status_code != 200:
        message = ''
        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            message = str(data['error'].get('message') or '')
        if response.status_code == 403 and 'has not been used' in message:
            raise CalendarError('The Google Calendar API is turned off in the Google Cloud project. An admin can '
                                'turn it on from Settings › Connected apps › Set up.')
        if response.status_code == 401:
            raise CalendarError('Google Calendar needs reconnecting in Settings › Connected apps.')
        raise CalendarError(f'Google Calendar refused the check ({response.status_code}).')
    return [e for e in (data.get('items') or []) if isinstance(e, dict)]


# ------------------------------------------------------------ settings and rules

async def calendar_connection(user_id: str) -> Any | None:
    from cognitrix.models.mcp_server import MCPServer

    for row in await MCPServer.find({'owner_id': str(user_id)}) or []:
        if (row.catalog_id or '') == CALENDAR_APP and (row.auth_type or '') == 'oauth':
            return row
    return None


async def settings_for(user_id: str) -> CalendarCallSettings:
    rows = await CalendarCallSettings.find({'user_id': str(user_id)}) or []
    if rows:
        return rows[0]
    row = CalendarCallSettings(user_id=str(user_id))
    await row.save()
    return row


async def save_settings(user_id: str, **fields: Any) -> CalendarCallSettings:
    row = await settings_for(user_id)
    changes: dict[str, Any] = {}
    if 'enabled' in fields and fields['enabled'] is not None:
        changes['enabled'] = bool(fields['enabled'])
    if fields.get('agent_id') is not None:
        changes['agent_id'] = str(fields['agent_id']).strip()
    if fields.get('auto_join') is not None:
        if fields['auto_join'] not in AUTO_JOIN:
            raise CalendarError('Choose to be asked every time, or to join the calls you organise.')
        changes['auto_join'] = fields['auto_join']
    if fields.get('never_words') is not None:
        changes['never_words'] = str(fields['never_words'])[:MAX_WORDS]
    if fields.get('remind_minutes') is not None:
        changes['remind_minutes'] = CalendarCallSettings._minutes(fields['remind_minutes'])
    if changes:
        await CalendarCallSettings.update_one({'id': row.id}, changes)
    return await settings_for(user_id)


def _words(settings: CalendarCallSettings) -> list[str]:
    return [w.strip().lower() for w in re.split(r'[\n,]', settings.never_words or '') if w.strip()]


def decide(settings: CalendarCallSettings, call: dict[str, Any]) -> tuple[str, str]:
    """(status, decided_by) for a call seen for the first time."""
    series = call.get('series_id') or ''
    if series and series in settings.never_series:
        return SKIPPED, 'series'
    if series and series in settings.always_series:
        return APPROVED, 'series'
    text = f"{call.get('title') or ''} {call.get('organizer') or ''}".lower()
    if any(word in text for word in _words(settings)):
        return SKIPPED, 'rule'
    if settings.auto_join == 'mine' and call.get('organised_by_me'):
        return APPROVED, 'rule'
    return SUGGESTED, ''


# ------------------------------------------------------------ scanning

async def sync(user_id: str, settings: CalendarCallSettings, events: list[dict[str, Any]],
               now: datetime) -> list[CalendarCall]:
    """Bring the person's stored calls in line with what the calendar shows."""
    stored = {row.event_id: row for row in await CalendarCall.find({'user_id': user_id}) or []}
    seen: set[str] = set()
    for event in events:
        call = to_call(event)
        if call is None or not call['event_id']:
            continue
        seen.add(call['event_id'])
        row = stored.get(call['event_id'])
        if row is None:
            status, by = decide(settings, call)
            row = CalendarCall(user_id=user_id, status=status, decided_by=by, **call)
            await row.save()
            stored[row.event_id] = row
            continue
        changes = {k: v for k, v in call.items() if getattr(row, k) != v}
        if 'starts_at' in changes and row.status == SUGGESTED:
            changes['alerted'] = False  # moved: remind again
        if changes:
            await CalendarCall.update_one({'id': row.id}, changes)
    horizon = (now + timedelta(hours=LOOKAHEAD_HOURS)).strftime(FMT)
    stamp, old = now.strftime(FMT), (now - timedelta(days=KEEP_DAYS)).strftime(FMT)
    for row in list(stored.values()):
        if row.ends_at and row.ends_at < old:
            await row.delete()
            stored.pop(row.event_id, None)
        elif row.event_id not in seen and row.status in OPEN and stamp < row.starts_at <= horizon:
            # Deleted, declined or moved out of the next day.
            await CalendarCall.update_one({'id': row.id}, {'status': CANCELLED})
    return await upcoming(user_id, now)


async def check(user_id: str, now: datetime | None = None) -> list[CalendarCall]:
    """Read one person's calendar now. Raises CalendarError."""
    from cognitrix.mcp import oauth

    now = now or utcnow()
    settings = await settings_for(user_id)
    row = await calendar_connection(user_id)
    if row is None:
        raise CalendarError('Connect Google Calendar in Settings › Connected apps first.')
    try:
        token = await oauth.access_token(row)
    except oauth.ReconnectNeeded:
        raise CalendarError('Google Calendar needs reconnecting in Settings › Connected apps.')
    except httpx.HTTPError:
        raise CalendarError("Couldn't reach Google to renew the Calendar sign-in.")
    events = await upcoming_events(token, now)
    calls = await sync(user_id, settings, events, now)
    await CalendarCallSettings.update_one({'id': settings.id}, {'checked_at': now.strftime(FMT), 'error': ''})
    return calls


async def upcoming(user_id: str, now: datetime | None = None) -> list[CalendarCall]:
    now = now or utcnow()
    stamp = now.strftime(FMT)
    rows = [r for r in await CalendarCall.find({'user_id': str(user_id)}) or [] if (r.ends_at or '') >= stamp]
    rows.sort(key=lambda r: r.starts_at)
    return rows


async def poll(now: datetime | None = None) -> int:
    """Check every connected calendar that is due, then act on due calls. Returns calls joined."""
    from cognitrix.models.mcp_server import MCPServer

    now = now or utcnow()
    due_before = (now - timedelta(seconds=check_seconds())).strftime(FMT)
    try:
        connections = await MCPServer.find({'catalog_id': CALENDAR_APP}) or []
    except Exception as exc:
        if 'no such table' in str(exc).lower():
            return 0
        raise
    owners = sorted({str(r.owner_id) for r in connections if r.owner_id and (r.auth_type or '') == 'oauth'})
    for owner in owners:
        try:
            settings = await settings_for(owner)
            if not settings.enabled or (settings.checked_at and settings.checked_at > due_before):
                continue
            # Claim the check, so two processes never both read it.
            claim: dict[str, Any] = {'id': settings.id, 'checked_at': settings.checked_at}
            if await CalendarCallSettings.update_one(claim, {'checked_at': now.strftime(FMT)}) != 1:
                continue
            await check(owner, now)
        except CalendarError as exc:
            await CalendarCallSettings.update_one({'user_id': owner}, {'error': str(exc)[:300]})
        except Exception:
            logger.exception('Calendar check failed for %s', owner)
    return await act(now)


# ------------------------------------------------------------ reminding and joining

def _local_time(call: CalendarCall) -> str:
    starts = _when(call.starts_at)
    return starts.strftime('%H:%M UTC') if starts else ''


async def _alert(call: CalendarCall) -> None:
    from cognitrix.meetings.live import PLATFORM_LABELS
    from cognitrix.tasks.automation import create_notification

    label = PLATFORM_LABELS.get(call.platform, 'call')
    await create_notification(
        call.user_id, f'Send an agent to "{call.title}"?',
        f'{label} at {_local_time(call)}{" with " + call.organizer if call.organizer else ""}. '
        'Open Meetings to send an agent or skip it.',
        link=f'/meetings?call={call.id}')


async def _join(call: CalendarCall, settings: CalendarCallSettings) -> bool:
    from cognitrix.api.routes.meetings import start_live
    from cognitrix.meetings import notes as meeting_notes
    from cognitrix.tasks.automation import create_notification

    # Claim it, so a tap and the scheduler can't both send an agent.
    if await CalendarCall.update_one({'id': call.id, 'status': APPROVED, 'meeting_id': ''},
                                     {'status': JOINED}) != 1:
        return False
    try:
        row = await start_live(call.user_id, url=call.url, agent_id=settings.agent_id, title=call.title)
    except meeting_notes.MeetingError as exc:
        await CalendarCall.update_one({'id': call.id}, {'status': FAILED, 'error': str(exc)[:300]})
        await create_notification(call.user_id, f"Couldn't send an agent to {call.title}", str(exc)[:300],
                                  link=f'/meetings?call={call.id}')
        return False
    except Exception:
        logger.exception('Joining calendar call %s failed', call.id)
        await CalendarCall.update_one({'id': call.id}, {'status': FAILED, 'error': 'Something went wrong.'})
        return False
    await CalendarCall.update_one({'id': call.id}, {'meeting_id': str(row.id), 'error': ''})
    return True


async def act(now: datetime | None = None, user_id: str | None = None) -> int:
    """Remind about suggestions that are close, and join approved calls that are due."""
    now = now or utcnow()
    joined = 0
    query: dict[str, Any] = {'user_id': user_id} if user_id else {}
    rows = [r for r in await CalendarCall.find(query) or [] if r.status in OPEN]
    settings_cache: dict[str, CalendarCallSettings] = {}
    for call in rows:
        starts, ends = _when(call.starts_at), _when(call.ends_at)
        if starts is None:
            continue
        settings = settings_cache.get(call.user_id) or await settings_for(call.user_id)
        settings_cache[call.user_id] = settings
        until = (starts - now).total_seconds()
        try:
            if call.status == SUGGESTED:
                if until < -MISSED_AFTER_S:
                    await CalendarCall.update_one({'id': call.id, 'status': SUGGESTED}, {'status': MISSED})
                elif not call.alerted and until <= settings.remind_minutes * 60 and settings.enabled:
                    if await CalendarCall.update_one({'id': call.id, 'alerted': call.alerted},
                                                     {'alerted': True}) == 1:
                        await _alert(call)
            elif call.status == APPROVED and not call.meeting_id:
                if ends is not None and now >= ends:
                    await CalendarCall.update_one({'id': call.id, 'status': APPROVED}, {'status': MISSED})
                elif until <= JOIN_EARLY_S:
                    joined += 1 if await _join(call, settings) else 0
        except Exception:
            logger.exception('Calendar call %s: could not act', call.id)
    return joined


async def answer(user_id: str, call_id: str, action: str, now: datetime | None = None) -> CalendarCall:
    """The person's choice for one call (Meetings page only). Raises CalendarError."""
    if action not in ACTIONS:
        raise CalendarError('Send an agent, skip it, or choose for the whole series.')
    call = await CalendarCall.get(call_id)
    if call is None or str(call.user_id) != str(user_id):
        raise CalendarError('That call is gone.')
    if call.status not in (*OPEN, SKIPPED, MISSED, FAILED, CANCELLED):
        raise CalendarError('An agent was already sent to this call.')
    now = now or utcnow()
    ends = _when(call.ends_at)
    if action in ('send', 'always') and ends is not None and now >= ends:
        raise CalendarError('That call is over.')
    settings = await settings_for(user_id)
    series = call.series_id
    if series and action in ('always', 'never', 'ask'):
        always = [s for s in settings.always_series if s != series]
        never = [s for s in settings.never_series if s != series]
        if action == 'always':
            always.append(series)
        elif action == 'never':
            never.append(series)
        await CalendarCallSettings.update_one({'id': settings.id}, {
            'always_series': json.dumps(always[-500:]), 'never_series': json.dumps(never[-500:])})
    status = {'send': APPROVED, 'always': APPROVED, 'skip': SKIPPED, 'never': SKIPPED, 'ask': SUGGESTED}[action]
    by = 'you' if action in ('send', 'skip') else ('series' if series else 'you')
    targets = [call]
    if series and action in ('always', 'never'):
        targets += [r for r in await CalendarCall.find({'user_id': str(user_id), 'series_id': series}) or []
                    if str(r.id) != str(call.id) and r.status in OPEN]
    for row in targets:
        await CalendarCall.update_one({'id': row.id}, {'status': status, 'decided_by': by, 'error': ''})
    if status == APPROVED:
        await act(now, user_id=str(user_id))
    return await CalendarCall.get(call_id)


def view(call: CalendarCall) -> dict[str, Any]:
    return {
        'id': str(call.id), 'title': call.title, 'starts_at': call.starts_at, 'ends_at': call.ends_at,
        'url': call.url, 'platform': call.platform, 'organizer': call.organizer,
        'organised_by_me': call.organised_by_me, 'series': bool(call.series_id), 'status': call.status,
        'decided_by': call.decided_by, 'meeting_id': call.meeting_id, 'error': call.error,
    }


def settings_view(row: CalendarCallSettings) -> dict[str, Any]:
    return {
        'enabled': row.enabled, 'agent_id': row.agent_id, 'auto_join': row.auto_join,
        'never_words': row.never_words, 'remind_minutes': row.remind_minutes,
        'series_choices': len(row.always_series) + len(row.never_series),
        'checked_at': row.checked_at, 'error': row.error,
    }
