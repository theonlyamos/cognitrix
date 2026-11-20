"""Token counts for one chat, shown in the chat's side panel.

Every model call inside a chat turn already goes through
``cognitrix.usage.log``; a turn hands it a sink (``turn_sink``) that also adds
the call to ``Session.token_usage``, which is saved with the chat:

    {
      'models': {'provider|model': {provider, model, calls, prompt_tokens, ...}},
      'last_turn': {'models': {...same shape...}, 'started': ISO time},
      'context': {'tokens', 'window', 'provider', 'model'},
    }

Counts are kept by model so the cost can be worked out with today's prices
when the panel asks (``report``), the same way the usage page does.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from cognitrix.usage import TOKEN_FIELDS, current_model

logger = logging.getLogger('cognitrix.log')


def _blank(provider: str, model: str) -> dict[str, Any]:
    return {'provider': provider, 'model': model, 'calls': 0, **{f: 0 for f in TOKEN_FIELDS}}


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _add(models: dict[str, Any], provider: str, model: str, counts: dict[str, int]) -> None:
    key = f'{provider}|{model}'
    row = models.get(key)
    if not isinstance(row, dict):
        row = models[key] = _blank(provider, model)
    row['calls'] = _int(row.get('calls')) + 1
    for field in TOKEN_FIELDS:
        row[field] = _int(row.get(field)) + counts[field]


def record(session: Any, usage: dict, provider: str, model: str, llm: Any = None) -> None:
    """Add one model call's usage to the chat's counts (in memory; the turn saves)."""
    counts = {field: _int(usage.get(field)) for field in TOKEN_FIELDS}
    if not counts['prompt_tokens'] and not counts['completion_tokens']:
        return
    data = session.token_usage if isinstance(session.token_usage, dict) else {}
    models = data.setdefault('models', {})
    _add(models, provider, model, counts)
    last = data.setdefault('last_turn', {'models': {}, 'started': datetime.now(UTC).isoformat()})
    _add(last.setdefault('models', {}), provider, model, counts)
    # The chat's own model's latest call is how full its context is: what it
    # read plus what it wrote, which the next call reads back.
    agent_provider = str(getattr(llm, 'provider', '') or '')
    agent_model = str(getattr(llm, 'model', '') or '')
    if llm is not None and (provider, model) == (agent_provider, agent_model):
        try:
            window = int(llm.get_context_window() or 0)
        except Exception:
            window = 0
        data['context'] = {
            'tokens': counts['prompt_tokens'] + counts['completion_tokens'],
            'window': window,
            'provider': provider,
            'model': model,
        }
    session.token_usage = data


def turn_sink(session: Any, llm: Any = None, previous: Any = None):
    """A ``cognitrix.usage.set_sink`` callback for one chat turn.

    Starts a fresh "last reply" tally and passes every call on to the sink
    that was already set (a task run's or a swarm's), so those still count.
    """
    data = session.token_usage if isinstance(session.token_usage, dict) else {}
    data['last_turn'] = {'models': {}, 'started': datetime.now(UTC).isoformat()}
    session.token_usage = data

    def sink(usage: dict) -> None:
        try:
            provider, model = current_model()
            record(session, usage, provider, model, llm)
        except Exception:
            logger.debug('chat token count failed', exc_info=True)
        if previous is not None:
            previous(usage)

    return sink


def _totals(models: dict[str, Any], prices: dict[str, Any]) -> dict[str, Any]:
    from cognitrix.usage import pricing

    total: dict[str, Any] = {'calls': 0, **{f: 0 for f in TOKEN_FIELDS}, 'cost': 0.0, 'unpriced_tokens': 0}
    for row in (models or {}).values():
        if not isinstance(row, dict):
            continue
        counts = {f: _int(row.get(f)) for f in TOKEN_FIELDS}
        total['calls'] += _int(row.get('calls'))
        for field, value in counts.items():
            total[field] += value
        cost = pricing.cost(prices, str(row.get('provider') or ''), str(row.get('model') or ''), counts)
        if cost is None:
            total['unpriced_tokens'] += counts['prompt_tokens'] + counts['completion_tokens']
        else:
            total['cost'] += cost
    total['cost'] = round(total['cost'], 6)
    return total


def _context_from_history(session: Any, llm: Any) -> dict[str, Any] | None:
    """Chats from before token counting: the newest reply's own numbers."""
    for message in reversed(session.chat or []):
        if isinstance(message, dict) and message.get('type') == 'turn_timing':
            tokens = _int(message.get('prompt_tokens')) + _int(message.get('completion_tokens'))
            if not tokens:
                return None
            window = 0
            if llm is not None:
                try:
                    window = int(llm.get_context_window() or 0)
                except Exception:
                    window = 0
            return {'tokens': tokens, 'window': window,
                    'provider': str(getattr(llm, 'provider', '') or ''),
                    'model': str(getattr(llm, 'model', '') or '')}
    return None


async def report(session: Any, llm: Any = None) -> dict[str, Any]:
    """What the side panel shows: chat totals, the last reply and the context."""
    from cognitrix.usage import pricing

    data = session.token_usage if isinstance(session.token_usage, dict) else {}
    models = data.get('models') or {}
    prices = await pricing.load() if models else {}
    last = data.get('last_turn') or {}
    last_models = last.get('models') or {}
    context = data.get('context') or _context_from_history(session, llm)
    if context and llm is not None and (context.get('provider'), context.get('model')) != (
        str(getattr(llm, 'provider', '') or ''), str(getattr(llm, 'model', '') or '')
    ):
        # The agent moved to another model since: its window is what counts now.
        try:
            context = {**context, 'window': int(llm.get_context_window() or 0), 'stale_model': True}
        except Exception:
            pass
    return {
        'tracked': bool(models),
        'prices_available': bool(prices),
        'total': _totals(models, prices),
        'last_turn': _totals(last_models, prices) if last_models else None,
        'by_model': sorted(
            ({**_totals({k: v}, prices), 'provider': v.get('provider'), 'model': v.get('model')}
             for k, v in models.items() if isinstance(v, dict)),
            key=lambda b: (-b['cost'], -b['prompt_tokens']),
        ),
        'context': context,
    }
