"""Model prices for the usage page.

Prices come from OpenRouter's public model list, which carries the list price
of nearly every hosted model, and are kept in the app folder for a day so the
page does not wait on the network. The request sends nothing about the person
or what they used. Turn "Look up model prices" off in Settings to stop the lookup; the
page then shows tokens without a cost. A model that is not in the list (a
local model's own name, a private deployment) has no cost either, apart from
Ollama, which runs on the person's own machine and is counted as free.

``list_cost`` answers a second question: what the same use would cost at API
list prices. Plan use (a ChatGPT plan, Claude Code, Codex and the other coding
agent CLIs) is paid by the plan, so its ``cost`` is 0 or unknown, but its list
price shows what the plan covered.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from cognitrix.app_settings import get as app_setting

logger = logging.getLogger('cognitrix.log')

SOURCE_URL = 'https://openrouter.ai/api/v1/models'
MAX_AGE = 24 * 60 * 60
RETRY_AFTER = 15 * 60

_cache: dict[str, Any] = {'at': 0.0, 'prices': {}, 'failed_at': 0.0}
_lock = asyncio.Lock()

_PREFIX = {'openai': 'openai', 'anthropic': 'anthropic', 'google': 'google', 'gemini': 'google',
           'xai': 'x-ai', 'mistral': 'mistralai', 'deepseek': 'deepseek'}


def lookup_enabled() -> bool:
    return app_setting('COGNITRIX_PRICE_LOOKUP', '1').strip().lower() not in ('0', 'false', 'off', 'no')


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


# Plans and coding agent CLIs: which API's list price their models carry.
_PLAN_PREFIX = {'chatgpt': 'openai', 'codex': 'openai', 'claude-code': 'anthropic',
                'gemini-cli': 'google', 'antigravity-cli': 'google'}
_FAMILY = re.compile(r'^(opus|sonnet|haiku|fable)$')


def _guess_prefix(model: str) -> str | None:
    model = model.lower()
    if model.startswith(('gpt-', 'o1', 'o3', 'o4', 'codex')):
        return 'openai'
    if model.startswith('claude') or _FAMILY.match(model):
        return 'anthropic'
    if model.startswith('gemini'):
        return 'google'
    return None


def _version(name: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r'\d+', name))


def _newest_family(prices: dict[str, dict[str, float]], family: str) -> dict[str, float] | None:
    """Claude Code's short names (opus, sonnet) cost what the newest such model costs."""
    names = [
        key for key in prices
        if key.startswith('anthropic/claude-') and ':' not in key
        and re.search(rf'(^|-){family}(-|$)', key.split('/', 1)[1])
    ]
    return prices[max(names, key=_version)] if names else None


def _price_of(prices: dict[str, dict[str, float]], provider: str, model: str) -> dict[str, float] | None:
    return next((prices[name] for name in _candidates(provider, model) if name in prices), None)


def _dollars(entry: dict[str, float], counts: dict[str, int]) -> float:
    cached = int(counts.get('cached_tokens') or 0)
    written = int(counts.get('cache_write_tokens') or 0)
    fresh = max(0, int(counts.get('prompt_tokens') or 0) - cached - written)
    return (
        fresh * entry['prompt']
        + cached * entry.get('cache_read', entry['prompt'])
        + written * entry.get('cache_write', entry['prompt'])
        + int(counts.get('completion_tokens') or 0) * entry['completion']
    )


def list_cost(prices: dict[str, dict[str, float]], provider: str, model: str, counts: dict[str, int]) -> float | None:
    """Dollars at API list prices, even for plan use. None when the model has no known price."""
    provider, model = (provider or '').lower(), re.sub(r'\[1m\]$', '', (model or '').lower())
    if provider == 'ollama':
        return 0.0
    entry = _price_of(prices, provider, model)
    if entry is None and (provider in _PLAN_PREFIX or provider in ('copilot-cli', 'opencode')):
        family = _FAMILY.match(model)
        prefix = _PLAN_PREFIX.get(provider) or _guess_prefix(model)
        if family:
            entry = _newest_family(prices, family.group(1))
        elif '/' in model:
            entry = _price_of(prices, 'openrouter', model)
        elif prefix:
            entry = _price_of(prices, prefix, model)
    return None if entry is None else _dollars(entry, counts)


def cost(prices: dict[str, dict[str, float]], provider: str, model: str, counts: dict[str, int]) -> float | None:
    """Dollars for these token counts, or None when the model has no known price."""
    if (provider or '').lower() in ('ollama', 'chatgpt'):
        # Local models, and a ChatGPT plan (usage comes out of the plan).
        return 0.0
    entry = _price_of(prices, provider, model)
    return None if entry is None else _dollars(entry, counts)
