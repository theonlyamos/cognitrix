"""Per-chat token counts for the chat side panel (cognitrix/usage/chat.py)."""
from types import SimpleNamespace

import pytest

from cognitrix import usage
from cognitrix.usage import chat as chat_usage
from cognitrix.usage import pricing
from tests.test_session_ownership_routes import _ctx, _initialize_routes_db, _json_body, _owned_session
from tests.test_usage import PRICES


class _LLM(SimpleNamespace):
    def get_context_window(self):
        return self.window


def _llm(provider='anthropic', model='claude-opus-5-5', window=200_000):
    return _LLM(provider=provider, model=model, window=window)


def _turn(session, llm, calls, previous=None):
    """Run model calls through the usage log the way a chat turn does."""
    token = usage.set_sink(chat_usage.turn_sink(session, llm, previous))
    try:
        for provider, model, counts in calls:
            usage.note_model(provider, model)
            usage.log(counts)
    finally:
        usage.reset_sink(token)


async def test_turns_add_up_by_model_and_keep_the_last_reply(monkeypatch):
    async def prices():
        return PRICES
    monkeypatch.setattr(pricing, 'load', prices)

    session = SimpleNamespace(token_usage=None, chat=[])
    llm = _llm()
    _turn(session, llm, [
        ('anthropic', 'claude-opus-5-5', {'prompt_tokens': 1000, 'completion_tokens': 100, 'cached_tokens': 800}),
    ])
    seen = []
    _turn(session, llm, [
        ('anthropic', 'claude-opus-5-5', {'prompt_tokens': 2000, 'completion_tokens': 200}),
        # A helper call on another model (say, the action reviewer) counts too,
        # but does not move the context meter.
        ('openai', 'my-private-model', {'prompt_tokens': 70, 'completion_tokens': 30}),
    ], previous=seen.append)
    # The sink that was already set (a task run's, a swarm's) still gets every call.
    assert len(seen) == 2

    report = await chat_usage.report(session, llm)
    assert report['tracked'] and report['prices_available']
    total = report['total']
    assert total['calls'] == 3
    assert total['prompt_tokens'] == 3070 and total['completion_tokens'] == 330
    assert total['cached_tokens'] == 800
    assert total['unpriced_tokens'] == 100
    expected = (200 * 4 + 800 * 0.2 + 100 * 20 + 2000 * 4 + 200 * 20) / 1_000_000
    assert total['cost'] == pytest.approx(expected, abs=1e-6)

    last = report['last_turn']
    assert last['calls'] == 2 and last['prompt_tokens'] == 2070 and last['completion_tokens'] == 230
    assert [m['model'] for m in report['by_model']] == ['claude-opus-5-5', 'my-private-model']
    assert report['context'] == {'tokens': 2200, 'window': 200_000,
                                 'provider': 'anthropic', 'model': 'claude-opus-5-5'}


async def test_old_chats_show_context_from_the_last_reply():
    session = SimpleNamespace(token_usage=None, chat=[
        {'role': 'assistant', 'content': 'hi'},
        {'role': 'system', 'type': 'turn_timing', 'duration': 1, 'prompt_tokens': 5000, 'completion_tokens': 40},
    ])
    report = await chat_usage.report(session, _llm(window=128_000))
    assert report['tracked'] is False and report['last_turn'] is None
    assert report['context']['tokens'] == 5040 and report['context']['window'] == 128_000


async def test_a_model_picked_for_the_turn_drives_the_context(monkeypatch):
    from cognitrix.providers.base import turn_llm_override

    async def prices():
        return {}
    monkeypatch.setattr(pricing, 'load', prices)
    session = SimpleNamespace(token_usage=None, chat=[])
    agent_llm = _llm(window=200_000)
    agent_llm.id = 'agent-llm'
    picked = _llm('openai', 'gpt-5.5', window=400_000)
    token = turn_llm_override.set(('agent-llm', picked))
    try:
        _turn(session, agent_llm, [('openai', 'gpt-5.5', {'prompt_tokens': 10, 'completion_tokens': 5})])
    finally:
        turn_llm_override.reset(token)
    # The report keeps the picked model's window, not the agent's default.
    report = await chat_usage.report(session, agent_llm)
    assert report['context']['model'] == 'gpt-5.5' and report['context']['window'] == 400_000
    assert report['prices_available'] is False and report['total']['unpriced_tokens'] == 15


async def test_history_summaries_count_but_leave_the_context_meter():
    session = SimpleNamespace(token_usage=None, chat=[])
    llm = _llm()
    token = usage.set_sink(chat_usage.turn_sink(session, llm))
    try:
        usage.note_model('anthropic', 'claude-opus-5-5')
        usage.log({'prompt_tokens': 50_000, 'completion_tokens': 500})
        with chat_usage.side_call():
            usage.log({'prompt_tokens': 30_000, 'completion_tokens': 2_000})
    finally:
        usage.reset_sink(token)
    assert session.token_usage['context']['tokens'] == 50_500
    report = await chat_usage.report(session, llm)
    assert report['total']['prompt_tokens'] == 80_000


async def test_usage_route_is_for_the_chats_owner_only(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)

    async def prices():
        return PRICES
    monkeypatch.setattr(pricing, 'load', prices)

    session = await _owned_session('user-1', 'agent-1', token_usage={
        'models': {'anthropic|claude-opus-5-5': {
            'provider': 'anthropic', 'model': 'claude-opus-5-5', 'calls': 2,
            'prompt_tokens': 1000, 'completion_tokens': 100, 'cached_tokens': 0, 'cache_write_tokens': 0}},
    })
    body = _json_body(await routes.get_chat_usage(str(session.id), _ctx('user-1')))
    assert body['tracked'] and body['total']['calls'] == 2
    assert body['total']['cost'] == pytest.approx((1000 * 4 + 100 * 20) / 1_000_000)

    with pytest.raises(HTTPException) as error:
        await routes.get_chat_usage(str(session.id), _ctx('user-2'))
    assert error.value.status_code == 404
