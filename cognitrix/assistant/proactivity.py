"""How proactive a person's assistant may be, and the guards that keep it quiet and cheap.

Everything the assistant does without being asked (check-ins, the morning
brief, follow-ups, meeting prep, stuck-work alerts, dream mode) goes through
here:

* ``level``: ``off`` (none of it), ``brief`` (the morning brief only),
  ``balanced`` (brief and check-ins, the default) or ``eager`` (the same, with
  more room to interrupt).
* ``should_interrupt``: whether an alert may push to the person's phone,
  desktop or chat app right now. Outside quiet hours and under the daily
  limit it may; otherwise it waits in the Inbox and the next brief.
* ``over_budget``: a daily token limit for proactive work, counted with a
  usage sink around every proactive model call.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

LEVELS = ('off', 'brief', 'balanced', 'eager')
DEFAULT_LEVEL = 'balanced'
DEFAULT_QUIET_START = '21:00'
DEFAULT_QUIET_END = '08:00'
DEFAULT_BRIEF_TIME = '08:00'
DEFAULT_MAX_PUSHES = 2
MAX_PUSHES_LIMIT = 20
DEFAULT_DAILY_TOKENS = 200_000
"""Roughly a brief and a handful of check-ins on a small model."""
MAX_DAILY_TOKENS = 5_000_000
INACTIVE_DAYS = 7
"""After this long without opening the app, proactive work pauses."""
SEEN_WRITE_EVERY_S = 300
MUTED_MAX = 30
FEEDBACK_KEEP = 40

URGENT = 'urgent'
NORMAL = 'normal'


def _text(value: Any) -> str:
    return '' if value is None else str(value)


class ProactivitySettings(Model):
    """One row per person. A missing row means the defaults."""
    user_id: str
    level: str = DEFAULT_LEVEL
    quiet_start: str = DEFAULT_QUIET_START
    quiet_end: str = DEFAULT_QUIET_END
    max_pushes: int = DEFAULT_MAX_PUSHES
    brief_time: str = DEFAULT_BRIEF_TIME
    daily_tokens: int = DEFAULT_DAILY_TOKENS
    """Token limit per day for proactive work; 0 means no limit."""
    timezone: str = ''
    """IANA name, from the app's X-Timezone header the last time the person was here."""
    last_seen_at: str = ''
    muted: str = '[]'
    """JSON list of short topics the person said not to show again."""
    feedback: str = '[]'
    """JSON list of recent {verdict, text, at} from briefs and offers, newest last."""
    last_brief_day: str = ''
    """Local date of the last morning brief started."""
    backoff: int = 1
    """1, 2 or 4: check-ins run this many times less often after repeated dismissals."""
    last_fingerprint: str = ''
    """What the free change check saw last time a check-in ran."""
    mail_cursor: str = ''
    """Gmail history id the main assistant's mail check read up to."""

    @field_validator('level', mode='before')
    @classmethod
    def _level(cls, value):
        return value if value in LEVELS else DEFAULT_LEVEL

    @field_validator('quiet_start', 'quiet_end', 'brief_time', 'timezone', 'last_seen_at', 'last_brief_day',
                     'last_fingerprint', 'mail_cursor', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)

    @field_validator('muted', 'feedback', mode='before')
    @classmethod
    def _lists(cls, value):
        if isinstance(value, list):
            return json.dumps(value)
        return value if isinstance(value, str) and value.strip().startswith('[') else '[]'

    @field_validator('max_pushes', 'daily_tokens', 'backoff', mode='before')
    @classmethod
    def _ints(cls, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class ProactivityDay(Model):
    """What proactive work did on one day (UTC), for limits and the cost line in Settings."""
    user_id: str
    day: str
    pushes: int = 0
    held: int = 0
    """Alerts kept in the Inbox instead of pushed (quiet hours or the limit)."""
    tokens: int = 0
    runs: int = 0
    skipped: int = 0
    """Check-ins the free change check skipped because nothing had changed."""
    alerted: str = '[]'
    """JSON list of alert keys already sent today (stuck-work dedupe)."""

    @field_validator('pushes', 'held', 'tokens', 'runs', 'skipped', mode='before')
    @classmethod
    def _ints(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @field_validator('alerted', mode='before')
    @classmethod
    def _alerted(cls, value):
        return value if isinstance(value, str) and value.strip().startswith('[') else '[]'


MODELS = (ProactivitySettings, ProactivityDay)


def _loads(text: str) -> list:
    try:
        value = json.loads(text or '[]')
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def utc_day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime('%Y-%m-%d')


def utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime('%Y-%m-%d %H:%M:%S')


# --- settings -------------------------------------------------------------------

async def get_settings(user_id: str) -> ProactivitySettings:
    try:
        row = await ProactivitySettings.find_one({'user_id': str(user_id)})
    except Exception as exc:
        if 'no such table' not in str(exc).lower():
            raise
        row = None
    if row is None:
        return ProactivitySettings(user_id=str(user_id))
    for field, default in (('max_pushes', DEFAULT_MAX_PUSHES), ('daily_tokens', DEFAULT_DAILY_TOKENS),
                           ('backoff', 1)):
        if getattr(row, field, None) is None:
            setattr(row, field, default)
    return row


def _clock(value: Any, label: str) -> str:
    text = str(value or '').strip()
    try:
        parsed = datetime.strptime(text, '%H:%M')
    except ValueError:
        raise ValueError(f'{label} must be a time like 08:00') from None
    return parsed.strftime('%H:%M')


async def save_settings(user_id: str, **fields: Any) -> ProactivitySettings:
    """Change settings. Raises ValueError with words for the person."""
    changes: dict[str, Any] = {}
    if fields.get('level') is not None:
        if fields['level'] not in LEVELS:
            raise ValueError('Choose Off, Brief only, Balanced or Eager')
        changes['level'] = fields['level']
    for key, label in (('quiet_start', 'Quiet hours start'), ('quiet_end', 'Quiet hours end'),
                       ('brief_time', 'The brief time')):
        if fields.get(key) is not None:
            changes[key] = _clock(fields[key], label)
    if fields.get('max_pushes') is not None:
        value = fields['max_pushes']
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_PUSHES_LIMIT:
            raise ValueError(f'Interruptions a day must be 0 to {MAX_PUSHES_LIMIT}')
        changes['max_pushes'] = value
    if fields.get('daily_tokens') is not None:
        value = fields['daily_tokens']
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_DAILY_TOKENS:
            raise ValueError(f'The daily limit must be 0 to {MAX_DAILY_TOKENS:,} tokens')
        changes['daily_tokens'] = value
    if fields.get('timezone') is not None:
        from cognitrix.common.clock import clean_timezone

        changes['timezone'] = clean_timezone(fields['timezone'])
    if fields.get('muted') is not None:
        muted = [str(m).strip()[:120] for m in fields['muted'] if str(m).strip()][:MUTED_MAX]
        changes['muted'] = json.dumps(muted)
    for key in ('last_brief_day', 'last_fingerprint', 'last_seen_at', 'mail_cursor'):
        if fields.get(key) is not None:
            changes[key] = str(fields[key])
    if fields.get('backoff') is not None:
        changes['backoff'] = max(1, min(4, int(fields['backoff'])))
    if fields.get('feedback') is not None:
        changes['feedback'] = json.dumps(list(fields['feedback'])[-FEEDBACK_KEEP:])
    row = await ProactivitySettings.find_one({'user_id': str(user_id)})
    if row is None:
        row = ProactivitySettings(user_id=str(user_id), **changes)
        await row.save()
    elif changes:
        await ProactivitySettings.update_one({'id': row.id}, changes)
    return await get_settings(user_id)


def muted_topics(settings: ProactivitySettings) -> list[str]:
    return [str(m) for m in _loads(settings.muted)]


def recent_feedback(settings: ProactivitySettings) -> list[dict[str, Any]]:
    return [f for f in _loads(settings.feedback) if isinstance(f, dict)]


def view(settings: ProactivitySettings) -> dict[str, Any]:
    return {
        'level': settings.level,
        'quiet_start': settings.quiet_start or DEFAULT_QUIET_START,
        'quiet_end': settings.quiet_end or DEFAULT_QUIET_END,
        'max_pushes': settings.max_pushes,
        'brief_time': settings.brief_time or DEFAULT_BRIEF_TIME,
        'daily_tokens': settings.daily_tokens,
        'timezone': settings.timezone or '',
        'muted': muted_topics(settings),
        'backoff': settings.backoff or 1,
    }


# --- time -----------------------------------------------------------------------

def zone_of(settings: ProactivitySettings) -> tzinfo:
    from cognitrix.common.clock import clean_timezone

    name = clean_timezone(settings.timezone)
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo or UTC


def local_now(settings: ProactivitySettings, now: datetime | None = None) -> datetime:
    """The person's wall clock. ``now`` is naive UTC or aware."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(zone_of(settings))


def _minutes(hhmm: str, fallback: str) -> int:
    try:
        parsed = datetime.strptime(hhmm or fallback, '%H:%M')
    except ValueError:
        parsed = datetime.strptime(fallback, '%H:%M')
    return parsed.hour * 60 + parsed.minute


def in_quiet_hours(settings: ProactivitySettings, now: datetime | None = None) -> bool:
    local = local_now(settings, now)
    current = local.hour * 60 + local.minute
    start = _minutes(settings.quiet_start, DEFAULT_QUIET_START)
    end = _minutes(settings.quiet_end, DEFAULT_QUIET_END)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def push_limit(settings: ProactivitySettings) -> int:
    base = settings.max_pushes if settings.max_pushes is not None else DEFAULT_MAX_PUSHES
    return max(base * 2, 4) if settings.level == 'eager' and base else base


def inactive(settings: ProactivitySettings, now: datetime | None = None) -> bool:
    """True when the person hasn't opened the app for INACTIVE_DAYS."""
    if not settings.last_seen_at:
        return False
    try:
        seen = datetime.strptime(settings.last_seen_at[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
    except ValueError:
        return False
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - seen > timedelta(days=INACTIVE_DAYS)


def allows(settings: ProactivitySettings, what: str) -> bool:
    """Whether the level allows ``what``: 'brief', 'checkins', 'push', 'dream' or 'prep'."""
    if settings.level == 'off':
        return what == 'dream'
    if settings.level == 'brief':
        return what in ('brief', 'dream', 'push')
    return True


_seen_cache: dict[str, float] = {}


async def touch(user_id: str, timezone_name: str = '') -> None:
    """The person is using the app: remember when, and their timezone.

    Writes at most every few minutes. Coming back after a long absence arms a
    catch-up brief (the brief runs again today)."""
    from cognitrix.common.clock import clean_timezone

    key = str(user_id)
    last = _seen_cache.get(key, 0.0)
    if time.monotonic() - last < SEEN_WRITE_EVERY_S:
        return
    _seen_cache[key] = time.monotonic()
    try:
        settings = await get_settings(key)
        fields: dict[str, Any] = {'last_seen_at': utc_stamp()}
        tz = clean_timezone(timezone_name)
        if tz and tz != settings.timezone:
            fields['timezone'] = tz
        if inactive(settings):
            fields['last_brief_day'] = ''
        await save_settings(key, **fields)
    except Exception:
        logger.debug('Could not note that %s is here', key, exc_info=True)


# --- the day's counters ---------------------------------------------------------

async def today_row(user_id: str, now: datetime | None = None) -> ProactivityDay:
    day = utc_day(now)
    try:
        row = await ProactivityDay.find_one({'user_id': str(user_id), 'day': day})
    except Exception as exc:
        if 'no such table' not in str(exc).lower():
            raise
        return ProactivityDay(user_id=str(user_id), day=day)
    if row is None:
        row = ProactivityDay(user_id=str(user_id), day=day)
        await row.save()
        row = await ProactivityDay.find_one({'user_id': str(user_id), 'day': day}) or row
    return row


async def bump(user_id: str, field: str, by: int = 1, now: datetime | None = None) -> None:
    try:
        row = await today_row(user_id, now)
        if row.id:
            await ProactivityDay.update_one({'id': row.id}, {field: int(getattr(row, field, 0) or 0) + by})
    except Exception:
        logger.debug('Could not count %s for %s', field, user_id, exc_info=True)


async def should_interrupt(user_id: str, urgency: str = NORMAL, *, now: datetime | None = None,
                           count: bool = True) -> bool:
    """Whether a proactive alert may push to the person right now.

    Never at level Off or in quiet hours. Urgent alerts (a deadline in the
    next few hours, something blocking work) skip the daily limit; the rest
    stop at it. ``count`` records the push (or the hold) for today."""
    settings = await get_settings(user_id)
    allowed = True
    if not allows(settings, 'push') or in_quiet_hours(settings, now):
        allowed = False
    elif urgency != URGENT:
        row = await today_row(user_id, now)
        allowed = row.pushes < push_limit(settings)
    if count:
        await bump(user_id, 'pushes' if allowed else 'held', now=now)
    return allowed


# --- tokens ---------------------------------------------------------------------

async def tokens_today(user_id: str, now: datetime | None = None) -> int:
    return int((await today_row(user_id, now)).tokens or 0)


async def over_budget(user_id: str, now: datetime | None = None) -> bool:
    settings = await get_settings(user_id)
    if not settings.daily_tokens:
        return False
    return await tokens_today(user_id, now) >= settings.daily_tokens


def usage_sink(user_id: str):
    """A ``cognitrix.usage.set_sink`` callback counting tokens as proactive work."""
    import asyncio

    def sink(usage: dict) -> None:
        try:
            spent = int(usage.get('prompt_tokens') or 0) + int(usage.get('completion_tokens') or 0)
        except (TypeError, ValueError):
            return
        if spent <= 0:
            return
        try:
            asyncio.get_running_loop().create_task(bump(user_id, 'tokens', spent))
        except RuntimeError:
            pass

    return sink


async def week(user_id: str, now: datetime | None = None) -> dict[str, int]:
    """Totals for the last 7 days: tokens, pushes, held alerts, runs and skipped check-ins."""
    now = now or datetime.now(UTC)
    days = {utc_day(now - timedelta(days=i)) for i in range(7)}
    try:
        rows = [r for r in await ProactivityDay.find({'user_id': str(user_id)}) or [] if r.day in days]
    except Exception:
        rows = []
    return {key: sum(int(getattr(r, key, 0) or 0) for r in rows)
            for key in ('tokens', 'pushes', 'held', 'runs', 'skipped')}


async def already_alerted(user_id: str, key: str, now: datetime | None = None) -> bool:
    row = await today_row(user_id, now)
    return key in _loads(row.alerted)


async def mark_alerted(user_id: str, key: str, now: datetime | None = None) -> None:
    row = await today_row(user_id, now)
    keys = _loads(row.alerted)
    if key not in keys and row.id:
        keys.append(key)
        await ProactivityDay.update_one({'id': row.id}, {'alerted': json.dumps(keys[-200:])})


# --- learning from feedback ------------------------------------------------------

BACKOFF_WINDOW = 6
BACKOFF_DISMISSED = 5


async def record_feedback(user_id: str, verdict: str, text: str, *, mute: str = '') -> ProactivitySettings:
    """Remember an answer to a brief line or an offer, and adjust.

    ``verdict`` is 'useful', 'not_useful' or 'dismissed'. When most of the
    last few answers were not useful, check-ins slow down (``backoff``
    doubles, up to four times less often); useful answers bring it back."""
    settings = await get_settings(user_id)
    items = recent_feedback(settings)
    items.append({'verdict': verdict, 'text': str(text or '')[:160], 'at': utc_stamp()})
    items = items[-FEEDBACK_KEEP:]
    window = items[-BACKOFF_WINDOW:]
    negative = sum(1 for f in window if f.get('verdict') in ('not_useful', 'dismissed'))
    backoff = settings.backoff or 1
    if len(window) >= BACKOFF_WINDOW and negative >= BACKOFF_DISMISSED:
        backoff = min(4, backoff * 2)
        items.append({'verdict': 'backoff', 'text': f'Checking in {backoff}x less often', 'at': utc_stamp()})
    elif verdict == 'useful' and backoff > 1:
        backoff = max(1, backoff // 2)
    fields: dict[str, Any] = {'feedback': items, 'backoff': backoff}
    if mute:
        muted = muted_topics(settings)
        if mute not in muted:
            muted.append(mute)
        fields['muted'] = muted
    return await save_settings(user_id, **fields)


def feedback_note(settings: ProactivitySettings) -> str:
    """What check-ins and briefs read about the person's past answers."""
    lines = []
    muted = muted_topics(settings)
    if muted:
        lines.append('Never bring these up (the person said not to): ' + '; '.join(muted))
    items = recent_feedback(settings)
    useful = [f['text'] for f in items if f.get('verdict') == 'useful' and f.get('text')][-6:]
    not_useful = [f['text'] for f in items if f.get('verdict') in ('not_useful', 'dismissed')
                  and f.get('text')][-6:]
    if useful:
        lines.append('Recently marked useful: ' + '; '.join(useful))
    if not_useful:
        lines.append('Recently marked not useful or dismissed (bring up less of this): '
                     + '; '.join(not_useful))
    if (settings.backoff or 1) > 1:
        lines.append(f'Because recent items were mostly not useful, check-ins run {settings.backoff} '
                     'times less often. Be more selective.')
    return '\n'.join(lines)
