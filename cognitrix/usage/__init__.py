"""Usage tracking: who spent how many tokens on which model.

A chat or task turn names its owner with ``set_owner``; every model call
inside it then lands in that person's daily totals (``UsageDay``). Calls made
with no owner (the command line, internal helpers) are not recorded.
"""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger('cognitrix.log')

TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'cached_tokens', 'cache_write_tokens')

_owner: ContextVar[dict[str, str] | None] = ContextVar('cognitrix_usage_owner', default=None)
_model: ContextVar[tuple[str, str] | None] = ContextVar('cognitrix_usage_model', default=None)
_lock = asyncio.Lock()
_pending: set[asyncio.Task] = set()


def set_owner(user_id: Any, agent_id: Any = None):
    """Bind the following model calls to a person. Returns a reset token."""
    value = {'user_id': str(user_id), 'agent_id': str(agent_id or '')} if user_id else None
    return _owner.set(value)


def reset_owner(token) -> None:
    _owner.reset(token)


def note_model(provider: str | None, model: str | None) -> None:
    """Called just before a model request so its usage can be attributed."""
    _model.set((str(provider or ''), str(model or '')))


def _today() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d')


async def add(user_id: str, agent_id: str, provider: str, model: str, usage: dict) -> None:
    from cognitrix.models.usage import UsageDay

    counts = {}
    for field in TOKEN_FIELDS:
        try:
            counts[field] = max(0, int(usage.get(field) or 0))
        except (TypeError, ValueError):
            counts[field] = 0
    if not counts['prompt_tokens'] and not counts['completion_tokens']:
        return
    key = {'user_id': user_id, 'day': _today(), 'agent_id': agent_id,
           'provider': provider, 'model': model}
    async with _lock:
        row = await UsageDay.find_one(key)
        if row is None:
            await UsageDay(**key, calls=1, **counts).save()
            return
        changes = {field: int(getattr(row, field, 0) or 0) + value for field, value in counts.items()}
        changes['calls'] = int(getattr(row, 'calls', 0) or 0) + 1
        await UsageDay.update_one({'id': row.id}, changes)


def log(usage: dict | None) -> None:
    """Record one model call's usage for the current owner, without waiting."""
    owner = _owner.get()
    if not owner or not usage:
        return
    provider, model = _model.get() or ('', '')

    async def _save() -> None:
        try:
            await add(owner['user_id'], owner['agent_id'], provider, model, dict(usage))
        except Exception:
            logger.exception('Could not record usage')

    try:
        task = asyncio.get_running_loop().create_task(_save())
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def flush() -> None:
    """Wait for writes still in flight (tests, shutdown)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def summary(user_id: str, days: int = 30) -> dict[str, Any]:
    """Totals for the last ``days`` days, by day, by model and by agent."""
    from cognitrix.models.usage import UsageDay
    from cognitrix.usage import pricing

    days = max(1, min(int(days or 30), 365))
    today = datetime.now(UTC).date()
    first = (today - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    rows = [r for r in await UsageDay.find({'user_id': str(user_id)}) if str(r.day) >= first]
    prices = await pricing.load()

    def blank() -> dict[str, Any]:
        return {'calls': 0, **{f: 0 for f in TOKEN_FIELDS}, 'cost': 0.0, 'unpriced_tokens': 0}

    total = blank()
    by_day = {(today - timedelta(days=i)).strftime('%Y-%m-%d'): blank() for i in range(days)}
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}

    for row in rows:
        counts = {f: int(getattr(row, f, 0) or 0) for f in TOKEN_FIELDS}
        cost = pricing.cost(prices, row.provider, row.model, counts)
        buckets = [
            total,
            by_day.setdefault(str(row.day), blank()),
            by_model.setdefault((row.provider, row.model), blank()),
            by_agent.setdefault(row.agent_id or '', blank()),
        ]
        for bucket in buckets:
            bucket['calls'] += int(row.calls or 0)
            for field, value in counts.items():
                bucket[field] += value
            if cost is None:
                bucket['unpriced_tokens'] += counts['prompt_tokens'] + counts['completion_tokens']
            else:
                bucket['cost'] += cost

    def done(bucket: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**extra, **bucket, 'cost': round(bucket['cost'], 6)}

    return {
        'days': days,
        'prices_available': bool(prices),
        'total': done(total),
        'by_day': [done(by_day[d], day=d) for d in sorted(by_day)],
        'by_model': sorted(
            (done(b, provider=p, model=m) for (p, m), b in by_model.items()),
            key=lambda b: (-b['cost'], -b['prompt_tokens']),
        ),
        'by_agent': sorted(
            (done(b, agent_id=a) for a, b in by_agent.items()),
            key=lambda b: (-b['cost'], -b['prompt_tokens']),
        ),
    }
