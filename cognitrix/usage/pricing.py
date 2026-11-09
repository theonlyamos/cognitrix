"""Model prices for the usage page.

Prices come from OpenRouter's public model list, which carries the list price
of nearly every hosted model, and are kept in the app folder for a day so the
page does not wait on the network. The request sends nothing about the person
or what they used. Set COGNITRIX_PRICE_LOOKUP=0 to turn the lookup off; the
page then shows tokens without a cost. A model that is not in the list (a
local model's own name, a private deployment) has no cost either, apart from
Ollama, which runs on the person's own machine and is counted as free.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger('cognitrix.log')

SOURCE_URL = 'https://openrouter.ai/api/v1/models'
MAX_AGE = 24 * 60 * 60
RETRY_AFTER = 15 * 60

_cache: dict[str, Any] = {'at': 0.0, 'prices': {}, 'failed_at': 0.0}
_lock = asyncio.Lock()

_PREFIX = {'openai': 'openai', 'anthropic': 'anthropic', 'google': 'google', 'gemini': 'google',
           'xai': 'x-ai', 'mistral': 'mistralai', 'deepseek': 'deepseek'}


def lookup_enabled() -> bool:
    return os.environ.get('COGNITRIX_PRICE_LOOKUP', '1').strip().lower() not in ('0', 'false', 'off', 'no')


def _cache_file():
    from cognitrix.config import settings
    return settings.workdir / 'model_prices.json'


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse(payload: Any) -> dict[str, dict[str, float]]:
    """OpenRouter's model list -> {model id: price per token}."""
    prices: dict[str, dict[str, float]] = {}
    for item in (payload or {}).get('data') or []:
        model_id = str(item.get('id') or '').lower()
        pricing = item.get('pricing') or {}
        prompt, completion = _number(pricing.get('prompt')), _number(pricing.get('completion'))
        if not model_id or prompt is None or completion is None:
            continue
        entry = {'prompt': prompt, 'completion': completion}
        for source, name in (('input_cache_read', 'cache_read'), ('input_cache_write', 'cache_write')):
            value = _number(pricing.get(source))
            if value is not None:
                entry[name] = value
        prices[model_id] = entry
    return prices


async def _fetch() -> dict[str, dict[str, float]]:
    import httpx

    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(SOURCE_URL)
        response.raise_for_status()
        return parse(response.json())


async def load() -> dict[str, dict[str, float]]:
    """Current prices, from memory, the saved copy or the network. {} if none."""
    if not lookup_enabled():
        return {}
    now = time.time()
    if _cache['prices'] and now - _cache['at'] < MAX_AGE:
        return _cache['prices']
    async with _lock:
        if _cache['prices'] and now - _cache['at'] < MAX_AGE:
            return _cache['prices']
        saved: dict[str, Any] = {}
        try:
            saved = json.loads(_cache_file().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        if saved.get('prices') and now - float(saved.get('at') or 0) < MAX_AGE:
            _cache.update(at=float(saved['at']), prices=saved['prices'])
            return _cache['prices']
        if now - _cache['failed_at'] > RETRY_AFTER:
            try:
                prices = await _fetch()
                if prices:
                    _cache.update(at=now, prices=prices)
                    try:
                        _cache_file().write_text(json.dumps({'at': now, 'prices': prices}), encoding='utf-8')
                    except OSError:
                        pass
                    return prices
            except Exception as exc:
                logger.warning('Could not fetch model prices: %s', exc)
            _cache['failed_at'] = now
        # An old copy beats no prices at all.
        if saved.get('prices'):
            _cache.update(at=now - MAX_AGE + RETRY_AFTER, prices=saved['prices'])
        return _cache['prices']


def _candidates(provider: str, model: str) -> list[str]:
    provider, model = (provider or '').lower(), (model or '').lower()
    if not model:
        return []
    if provider == 'openrouter' or '/' in model:
        return [model, model.split(':')[0]]
    prefix = _PREFIX.get(provider)
    if not prefix:
        return []
    names = [model]
    # Anthropic writes versions with dashes (claude-opus-5-5), the list with a dot.
    dotted = re.sub(r'-(\d)-(\d)(?=$|-)', r'-\1.\2', model)
    if dotted != model:
        names.append(dotted)
    # Dated snapshots (…-20260101, …-2026-01-01) cost what the undated name costs.
    for name in list(names):
        undated = re.sub(r'-(\d{8}|\d{4}-\d{2}-\d{2})$', '', name)
        if undated != name:
            names.append(undated)
            names.append(re.sub(r'-(\d)-(\d)(?=$|-)', r'-\1.\2', undated))
    return [f'{prefix}/{name}' for name in names]


def cost(prices: dict[str, dict[str, float]], provider: str, model: str, counts: dict[str, int]) -> float | None:
    """Dollars for these token counts, or None when the model has no known price."""
    if (provider or '').lower() == 'ollama':
        return 0.0
    entry = next((prices[name] for name in _candidates(provider, model) if name in prices), None)
    if entry is None:
        return None
    cached = int(counts.get('cached_tokens') or 0)
    written = int(counts.get('cache_write_tokens') or 0)
    fresh = max(0, int(counts.get('prompt_tokens') or 0) - cached - written)
    return (
        fresh * entry['prompt']
        + cached * entry.get('cache_read', entry['prompt'])
        + written * entry.get('cache_write', entry['prompt'])
        + int(counts.get('completion_tokens') or 0) * entry['completion']
    )
