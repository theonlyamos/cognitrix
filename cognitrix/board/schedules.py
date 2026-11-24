"""Scheduled posts: a cron is just a repeating message to an agent.

"reviewer: every weekday 9:00, check open PRs" is a schedule on a board that
posts a request for the reviewer at 9:00 on Monday to Friday, in the
person's time zone. The post wakes the agent like any request does, and its
answer lands on the board. Runs from the scheduler (``run_due``).
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cognitrix.board.models import BoardSchedule

logger = logging.getLogger('cognitrix.log')

SCHEDULES_MAX = 50
CHECK_MS = 60 * 1000

_DAY_NAMES = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
_GROUPS = {'weekdays': [0, 1, 2, 3, 4], 'weekday': [0, 1, 2, 3, 4], 'workdays': [0, 1, 2, 3, 4],
           'weekends': [5, 6], 'weekend': [5, 6], 'daily': list(range(7)), 'day': list(range(7)),
           'everyday': list(range(7))}
DAY_LABELS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _service():
    from cognitrix.board import service

    return service


def parse_when(when: str) -> tuple[list[int], str]:
    """'weekdays 09:00', 'daily 6pm', 'mon,wed 17:30', 'every friday at 4:15 pm' -> (days, 'HH:MM')."""
    text = str(when or '').lower().replace(',', ' ').replace('every', ' ').replace(' at ', ' ')
    match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    if not match:
        raise _service().BoardError('bad_when', 'Say when, like "weekdays 09:00" or "mon,thu 17:30".')
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if match.group(3) == 'pm' and hour < 12:
        hour += 12
    if match.group(3) == 'am' and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _service().BoardError('bad_when', 'The time should be like 09:00 or 5:30 pm.')
    days: list[int] = []
    for word in re.findall(r'[a-z]+', text[:match.start()] + ' ' + text[match.end():]):
        if word in _GROUPS:
            days += _GROUPS[word]
        elif word[:3] in _DAY_NAMES and word not in ('am', 'pm'):
            days.append(_DAY_NAMES[word[:3]])
    days = sorted(set(days)) or list(range(7))
    return days, f'{hour:02d}:{minute:02d}'


def describe(days: list[int], time: str) -> str:
    if days == [0, 1, 2, 3, 4]:
        which = 'weekdays'
    elif len(days) == 7:
        which = 'every day'
    elif days == [5, 6]:
        which = 'weekends'
    else:
        which = ', '.join(DAY_LABELS[d] for d in days)
    return f'{which} at {time}'


async def _zone(user_key: str, tz: str = ''):
    if tz:
        try:
            return ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        from cognitrix.assistant import proactivity

        return proactivity.zone_of(await proactivity.get_settings(str(user_key)))
    except Exception:
        return UTC


def next_time(days: list[int], time: str, zone: Any, after: datetime) -> int:
    """The next run after ``after`` (aware), in ms."""
    hour, minute = (int(x) for x in time.split(':'))
    local = after.astimezone(zone)
    for add in range(0, 8):
        day = (local + timedelta(days=add)).date()
        if day.weekday() not in days:
            continue
        at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if at > local:
            return int(at.astimezone(UTC).timestamp() * 1000)
    return int((after + timedelta(days=7)).timestamp() * 1000)


def public(row: BoardSchedule) -> dict[str, Any]:
    service = _service()
    return {'id': str(row.id), 'board': row.board, 'thread': row.thread, 'to': row.to_name, 'kind': row.kind,
            'text': row.text, 'days': list(row.days), 'time': row.at_time, 'tz': row.tz, 'enabled': row.enabled,
            'when': describe(list(row.days), row.at_time), 'next_at': service.iso(row.next_at),
            'last_at': service.iso(row.last_at), 'last_post': row.last_post, 'created_by': row.created_by}


async def list_schedules(user_key: str, board: str = '') -> list[BoardSchedule]:
    where: dict[str, Any] = {'user_id': str(user_key)}
    if board:
        where['board'] = board
    rows = await BoardSchedule.find(where) or []
    return sorted(rows, key=lambda r: (r.board, r.at_time, str(r.id)))


async def save_schedule(user_key: str, *, board: str, text: str, to: str = '', kind: str = 'request',
                        when: str = '', days: list[int] | None = None, time: str = '', tz: str = '',
                        thread: str = '', enabled: bool = True, created_by: str = '',
                        schedule_id: str = '') -> BoardSchedule:
    service = _service()
    board = service.clean_board(board)
    kind = service.clean_kind(kind or 'request')
    text = str(text or '').strip()
    if not text:
        raise service.BoardError('text_required', 'Write what the repeating post says.')
    if len(text) > service.TEXT_MAX:
        raise service.BoardError('too_long', f'A post holds at most {service.TEXT_MAX} characters.')
    if when:
        days, time = parse_when(when)
    else:
        days = sorted({int(d) for d in days or [] if 0 <= int(d) <= 6}) or list(range(7))
        if not re.fullmatch(r'\d{2}:\d{2}', str(time or '')):
            raise service.BoardError('bad_when', 'The time should be like 09:00.')
        hour, minute = (int(x) for x in time.split(':'))
        if hour > 23 or minute > 59:
            raise service.BoardError('bad_when', 'The time should be like 09:00.')
    # Check "to" now, so a typo shows up when saving, not at 9 tomorrow.
    await service._resolve_to(user_key, board, to, service.PERSON) if str(to or '').strip() else None
    zone = await _zone(user_key, tz)
    data = {'board': board, 'thread': service.clean_thread(thread), 'to_name': str(to or '').strip().lstrip('@'),
            'kind': kind, 'text': text, 'days': days, 'at_time': time, 'tz': str(tz or ''), 'enabled': bool(enabled),
            'next_at': next_time(days, time, zone, datetime.now(UTC))}
    if schedule_id:
        row = await BoardSchedule.get(str(schedule_id))
        if row is None or row.user_id != str(user_key):
            raise service.BoardError('not_found', 'No such schedule.')
        await BoardSchedule.update_one({'id': row.id}, data)
        for key, value in data.items():
            setattr(row, key, value)
        return row
    if len(await list_schedules(user_key)) >= SCHEDULES_MAX:
        raise service.BoardError('too_many', f'At most {SCHEDULES_MAX} scheduled posts.')
    row = BoardSchedule(user_id=str(user_key), created_by=str(created_by or ''), **data)
    await row.save()
    return row


async def delete_schedule(user_key: str, schedule_id: str) -> None:
    row = await BoardSchedule.get(str(schedule_id or ''))
    if row is None or row.user_id != str(user_key):
        raise _service().BoardError('not_found', 'No such schedule.')
    await BoardSchedule.delete_many({'id': row.id})


_last_check = 0


async def run_due(now: datetime | None = None, *, force: bool = False) -> int:
    """Post every schedule whose time has come (from the scheduler, once a minute at most)."""
    global _last_check
    service = _service()
    now = now or datetime.now(UTC)
    stamp = int(now.timestamp() * 1000)
    if not force and stamp - _last_check < CHECK_MS:
        return 0
    _last_check = stamp
    count = 0
    for row in await BoardSchedule.find({}) or []:
        if not row.enabled or not row.next_at or row.next_at > stamp:
            continue
        zone = await _zone(row.user_id, row.tz)
        following = next_time(list(row.days), row.at_time, zone, now)
        await BoardSchedule.update_one({'id': row.id}, {'next_at': following, 'last_at': stamp})
        if stamp - row.next_at > 6 * 3_600_000:
            continue  # the server was off: skip a run that is hours late rather than post it now
        try:
            from cognitrix.board import channels

            author = await channels.person(row.user_id)
            post = await service.create_post(row.user_id, board=row.board, kind=row.kind, text=row.text,
                                             to=row.to_name, author=author, thread=row.thread,
                                             tags=['scheduled'])
            await BoardSchedule.update_one({'id': row.id}, {'last_post': str(post.id)})
            count += 1
        except Exception:
            logger.exception('Could not post scheduled board post %s', row.id)
    return count
