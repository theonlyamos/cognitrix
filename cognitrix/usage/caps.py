"""Spending caps: how much one person's agents may use in a calendar month.

An admin sets a cap per person in Settings > People, and a default cap that
covers every member without one of their own. A cap is a dollar amount, a
token count, or both. Everything that runs as the person counts: chats,
tasks, check-ins and every swarm worker, because they all record usage under
the person (``cognitrix.usage.set_owner``).

When the month's usage reaches a cap, model calls made for that person stop
with a plain message until the month ends (UTC) or an admin raises the cap.
At 80% the person gets one Inbox alert per month.

Cost is worked out from list prices (``cognitrix.usage.pricing``). A model with
no known price adds no cost, so a token cap is the way to limit those.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

DEFAULT_KEY = '*'
"""The row holding the default cap for members."""

WARN_AT = 0.8
CACHE_SECONDS = 15.0

MODES = ('default', 'custom', 'none')


class SpendingCap(Model):
    user_id: str
    """A person's principal key, or ``*`` for the default for members."""
    mode: str = 'custom'
    """default: follow the default cap; custom: the numbers below; none: no cap."""
    monthly_cost: float = 0.0
    """US dollars per calendar month; 0 means no dollar cap."""
    monthly_tokens: int = 0
    """Prompt plus completion tokens per calendar month; 0 means no token cap."""
    warned_month: str = ''
    """'YYYY-MM' the 80% alert was last sent, so it goes once a month."""
    updated_by: str = ''

    @field_validator('monthly_cost', mode='before')
    @classmethod
    def _cost(cls, value):
        try:
            return max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0

    @field_validator('monthly_tokens', mode='before')
    @classmethod
    def _tokens(cls, value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @field_validator('mode', 'warned_month', 'updated_by', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)


_cache: dict[str, tuple[float, str | None]] = {}


def forget(user_id: str | None = None) -> None:
    """Drop cached answers (after a cap changes)."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(str(user_id), None)


def month() -> str:
    return datetime.now(UTC).strftime('%Y-%m')


def resets_on() -> str:
    now = datetime.now(UTC)
    year, next_month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return f'{year:04d}-{next_month:02d}-01'


def _limits(row: SpendingCap | None) -> dict[str, Any]:
    if row is None:
        return {'monthly_cost': 0.0, 'monthly_tokens': 0}
    return {'monthly_cost': round(float(row.monthly_cost or 0), 2), 'monthly_tokens': int(row.monthly_tokens or 0)}


async def get_row(user_id: str) -> SpendingCap | None:
    return await SpendingCap.find_one({'user_id': str(user_id)})


async def save(user_id: str, *, mode: str, monthly_cost: float = 0.0, monthly_tokens: int = 0,
               by: str = '') -> SpendingCap:
    """Set a person's cap (or the default, with ``DEFAULT_KEY``)."""
    if mode not in MODES:
        raise ValueError(f'mode must be one of {", ".join(MODES)}')
    if user_id == DEFAULT_KEY and mode != 'custom':
        mode = 'custom'
    values = {'mode': mode, 'monthly_cost': max(0.0, float(monthly_cost or 0)),
              'monthly_tokens': max(0, int(monthly_tokens or 0)), 'updated_by': by}
    row = await get_row(user_id)
    if row is None:
        await SpendingCap(user_id=str(user_id), **values).save()
    else:
        await SpendingCap.update_one({'id': row.id}, values)
    forget()
    return await get_row(user_id)  # type: ignore[return-value]


async def effective(user_id: str, *, is_admin: bool | None = None) -> dict[str, Any]:
    """The cap that applies to a person and where it comes from."""
    own = await get_row(user_id)
    mode = own.mode if own is not None and own.mode in MODES else 'default'
    if mode == 'custom':
        return {'source': 'own', 'mode': mode, **_limits(own)}
    if mode == 'none':
        return {'source': 'none', 'mode': mode, **_limits(None)}
    if is_admin is None:
        is_admin = await _is_admin(user_id)
    if is_admin:
        # The default is for members; an admin is capped only by their own.
        return {'source': 'none', 'mode': 'default', **_limits(None)}
    default = await get_row(DEFAULT_KEY)
    return {'source': 'default' if default is not None and (default.monthly_cost or default.monthly_tokens) else 'none',
            'mode': 'default', **_limits(default)}


async def _is_admin(user_id: str) -> bool:
    from cognitrix.agents import access as agent_access
    from cognitrix.models import User

    try:
        user = await User.get(user_id)
    except Exception:
        user = None
    if user is None:
        user = await User.find_one({'email': user_id})
    return bool(user is not None and await agent_access.is_admin(user))


async def month_used(user_id: str) -> dict[str, Any]:
    """This calendar month's usage for a person (UTC)."""
    from cognitrix.models.usage import UsageDay
    from cognitrix.usage import TOKEN_FIELDS, pricing

    first = month() + '-01'
    rows = [r for r in await UsageDay.find({'user_id': str(user_id)}) if str(r.day) >= first]
    prices = await pricing.load()
    used = {'cost': 0.0, 'tokens': 0, 'unpriced_tokens': 0, 'calls': 0}
    for row in rows:
        counts = {f: int(getattr(row, f, 0) or 0) for f in TOKEN_FIELDS}
        tokens = counts['prompt_tokens'] + counts['completion_tokens']
        used['tokens'] += tokens
        used['calls'] += int(row.calls or 0)
        cost = pricing.cost(prices, row.provider, row.model, counts)
        if cost is None:
            used['unpriced_tokens'] += tokens
        else:
            used['cost'] += cost
    used['cost'] = round(used['cost'], 6)
    return used


def _fraction(cap: dict[str, Any], used: dict[str, Any]) -> float:
    parts = []
    if cap.get('monthly_cost'):
        parts.append(used['cost'] / float(cap['monthly_cost']))
    if cap.get('monthly_tokens'):
        parts.append(used['tokens'] / float(cap['monthly_tokens']))
    return max(parts) if parts else 0.0


async def status(user_id: str, *, is_admin: bool | None = None) -> dict[str, Any]:
    """The cap, this month's usage, and whether new model calls are stopped."""
    cap = await effective(user_id, is_admin=is_admin)
    used = await month_used(user_id)
    fraction = _fraction(cap, used)
    return {
        'cap': cap,
        'used': used,
        'fraction': round(fraction, 4),
        'reached': fraction >= 1.0,
        'month': month(),
        'resets_on': resets_on(),
    }


def reached_message(state: dict[str, Any]) -> str:
    cap = state['cap']
    limits = []
    if cap.get('monthly_cost'):
        limits.append(f"${cap['monthly_cost']:,.2f}")
    if cap.get('monthly_tokens'):
        limits.append(f"{cap['monthly_tokens']:,} tokens")
    return (
        f"You've reached your spending limit for this month ({' / '.join(limits)}). "
        f"Agents can't use a model for you again until {state['resets_on']}, "
        'unless an admin raises your limit in Settings > People.'
    )


async def blocked_message(user_id: str | None) -> str | None:
    """A message to show instead of a model call when the person is over
    their cap, else None. Answers are cached for a few seconds, so a burst of
    calls (a swarm) reads the database once. A failure to check never blocks."""
    if not user_id:
        return None
    key = str(user_id)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    message: str | None = None
    try:
        cap = await effective(key)
        if cap['monthly_cost'] or cap['monthly_tokens']:
            state = await status(key)
            if state['reached']:
                message = reached_message(state)
            elif state['fraction'] >= WARN_AT:
                await _warn_once(key, state)
    except Exception:
        logger.debug('Could not check the spending cap for %s', key, exc_info=True)
        message = None
    _cache[key] = (now, message)
    return message


async def _warn_once(user_id: str, state: dict[str, Any]) -> None:
    row = await get_row(user_id)
    current = month()
    # The default cap's row is shared, so a member following it keeps their
    # "warned" mark on a row of their own (mode default).
    if row is not None and row.warned_month == current:
        return
    if row is None:
        await SpendingCap(user_id=user_id, mode='default', warned_month=current).save()
    else:
        await SpendingCap.update_one({'id': row.id}, {'warned_month': current})
    from cognitrix.tasks.automation import create_notification

    percent = int(state['fraction'] * 100)
    await create_notification(
        user_id, 'Spending limit almost reached',
        f"Your agents have used {percent}% of this month's spending limit. "
        f"When it runs out, they stop using models for you until {state['resets_on']}.",
        link='/settings/usage',
    )
