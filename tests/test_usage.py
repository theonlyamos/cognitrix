"""Usage totals: recording per person and the cost worked out from list prices."""
import pytest

from cognitrix import usage
from cognitrix.usage import pricing
from tests.test_session_ownership_routes import _ctx, _initialize_routes_db

PRICES = pricing.parse({'data': [
    {'id': 'anthropic/claude-opus-5.5', 'pricing': {
        'prompt': '0.000004', 'completion': '0.00002',
        'input_cache_read': '0.0000002', 'input_cache_write': '0.000005'}},
    {'id': 'openai/gpt-5.5', 'pricing': {'prompt': '0.000005', 'completion': '0.00003'}},
    {'id': 'broken/model', 'pricing': {'prompt': 'n/a', 'completion': '-1'}},
]})


def test_prices_parse_and_match_provider_model_names():
    assert 'broken/model' not in PRICES
    counts = {'prompt_tokens': 1_000_000, 'completion_tokens': 100_000}
    assert pricing.cost(PRICES, 'anthropic', 'claude-opus-5-5', counts) == pytest.approx(6.0)
    assert pricing.cost(PRICES, 'anthropic', 'claude-opus-5-5-20260901', counts) == pytest.approx(6.0)
    assert pricing.cost(PRICES, 'openrouter', 'openai/gpt-5.5', counts) == pytest.approx(8.0)
    assert pricing.cost(PRICES, 'openai', 'some-private-model', counts) is None
    assert pricing.cost(PRICES, 'ollama', 'llama3', counts) == 0.0


def test_cached_tokens_are_billed_at_the_cache_price():
    counts = {'prompt_tokens': 1_000_000, 'completion_tokens': 0,
              'cached_tokens': 900_000, 'cache_write_tokens': 50_000}
    # 50k fresh at $4, 900k read at $0.20, 50k written at $5 (per million).
    assert pricing.cost(PRICES, 'anthropic', 'claude-opus-5-5', counts) == pytest.approx(0.2 + 0.18 + 0.25)


async def test_model_calls_land_in_the_owners_totals(tmp_path, monkeypatch):
    from cognitrix.api.routes import usage as routes

    await _initialize_routes_db(tmp_path)
    from cognitrix.agents import Agent
    from cognitrix.models.usage import UsageDay
    await UsageDay._create_table_async()
    await Agent._create_table_async()

    async def prices():
        return PRICES
    monkeypatch.setattr(pricing, 'load', prices)

    token = usage.set_owner('user-1', 'agent-1')
    try:
        usage.note_model('anthropic', 'claude-opus-5-5')
        usage.log({'prompt_tokens': 1000, 'completion_tokens': 100, 'cached_tokens': 800})
        usage.log({'prompt_tokens': 500, 'completion_tokens': 50})
        usage.note_model('openai', 'my-private-model')
        usage.log({'prompt_tokens': 70, 'completion_tokens': 30})
    finally:
        usage.reset_owner(token)
    # No owner: a command-line call is not anybody's usage.
    usage.log({'prompt_tokens': 999, 'completion_tokens': 999})
    await usage.flush()

    mine = await routes.get_usage(30, _ctx('user-1'))
    assert mine['total']['calls'] == 3
    assert mine['total']['prompt_tokens'] == 1570 and mine['total']['cached_tokens'] == 800
    assert mine['total']['unpriced_tokens'] == 100
    expected = (700 * 4 + 800 * 0.2 + 150 * 20) / 1_000_000
    assert mine['total']['cost'] == pytest.approx(expected, abs=1e-6)
    assert [m['model'] for m in mine['by_model']] == ['claude-opus-5-5', 'my-private-model']
    assert len(mine['by_day']) == 30 and mine['by_day'][-1]['calls'] == 3
    assert mine['by_agent'][0]['agent_id'] == 'agent-1'

    theirs = await routes.get_usage(30, _ctx('user-2'))
    assert theirs['total']['calls'] == 0 and theirs['by_model'] == []


async def test_price_lookup_can_be_switched_off(monkeypatch):
    monkeypatch.setenv('COGNITRIX_PRICE_LOOKUP', '0')
    assert await pricing.load() == {}
