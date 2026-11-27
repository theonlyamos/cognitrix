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
    # 800 cached tokens read at $0.20 instead of $4 a million.
    assert mine['total']['cache_saved'] == pytest.approx(800 * 3.8 / 1_000_000, abs=1e-6)

    theirs = await routes.get_usage(30, _ctx('user-2'))
    assert theirs['total']['calls'] == 0 and theirs['by_model'] == []


async def test_price_lookup_can_be_switched_off(monkeypatch):
    monkeypatch.setenv('COGNITRIX_PRICE_LOOKUP', '0')
    assert await pricing.load() == {}


def test_plan_use_gets_an_api_list_price():
    prices = {**PRICES, **pricing.parse({'data': [
        {'id': 'anthropic/claude-opus-4.5', 'pricing': {'prompt': '0.000001', 'completion': '0.000001'}},
        {'id': 'anthropic/claude-sonnet-5', 'pricing': {'prompt': '0.000003', 'completion': '0.000015'}},
    ]})}
    counts = {'prompt_tokens': 1_000_000, 'completion_tokens': 100_000}
    # Plan use is not billed, but has a list price.
    assert pricing.cost(prices, 'chatgpt', 'gpt-5.5', counts) == 0.0
    assert pricing.list_cost(prices, 'chatgpt', 'gpt-5.5', counts) == pytest.approx(8.0)
    assert pricing.list_cost(prices, 'codex', 'gpt-5.5', counts) == pytest.approx(8.0)
    # Claude Code's short names cost what the newest model of that family costs.
    assert pricing.cost(prices, 'claude-code', 'opus', counts) is None
    assert pricing.list_cost(prices, 'claude-code', 'opus', counts) == pytest.approx(6.0)
    assert pricing.list_cost(prices, 'claude-code', 'opus[1m]', counts) == pytest.approx(6.0)
    assert pricing.list_cost(prices, 'claude-code', 'sonnet', counts) == pytest.approx(4.5)
    assert pricing.list_cost(prices, 'claude-code', 'claude-opus-5-5', counts) == pytest.approx(6.0)
    assert pricing.list_cost(prices, 'claude-code', 'haiku', counts) is None
    assert pricing.list_cost(prices, 'opencode', 'openai/gpt-5.5', counts) == pytest.approx(8.0)
    # Billed models cost the same either way.
    assert pricing.list_cost(prices, 'anthropic', 'claude-opus-5-5', counts) == pytest.approx(6.0)
    assert pricing.list_cost(prices, 'ollama', 'llama3', counts) == 0.0


async def test_days_split_by_model_and_agent_with_list_prices(tmp_path, monkeypatch):
    await _initialize_routes_db(tmp_path)
    from cognitrix.models.usage import UsageDay
    await UsageDay._create_table_async()

    async def prices():
        return PRICES
    monkeypatch.setattr(pricing, 'load', prices)

    await usage.add('user-3', 'agent-a', 'chatgpt', 'gpt-5.5', {'prompt_tokens': 1_000_000, 'completion_tokens': 0})
    await usage.add('user-3', 'agent-b', 'anthropic', 'claude-opus-5-5', {'prompt_tokens': 1_000_000, 'completion_tokens': 0})
    await usage.add('user-3', 'agent-b', 'claude-code', 'mystery', {'prompt_tokens': 10, 'completion_tokens': 5})

    result = await usage.summary('user-3', 7)
    total = result['total']
    assert total['cost'] == pytest.approx(4.0) and total['api_cost'] == pytest.approx(9.0)
    assert total['unpriced_tokens'] == 15 and total['api_unpriced_tokens'] == 15
    assert total['cache_saved'] == 0.0
    today = result['by_day'][-1]
    assert set(today['models']) == {'chatgpt/gpt-5.5', 'anthropic/claude-opus-5-5', 'claude-code/mystery'}
    assert today['models']['chatgpt/gpt-5.5']['api_cost'] == pytest.approx(5.0)
    assert today['models']['chatgpt/gpt-5.5']['cost'] == 0.0
    assert today['agents']['agent-b']['prompt_tokens'] == 1_000_010
    assert result['by_day'][0]['models'] == {}
    # Sorted by what the use is worth, so plan use is not buried at the bottom.
    assert [m['model'] for m in result['by_model']][:2] == ['gpt-5.5', 'claude-opus-5-5']
