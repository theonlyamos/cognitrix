"""OpenAI request params: GPT-5.x / o-series reject `max_tokens` and any
non-default temperature, so the provider layer must adapt the request."""

import pytest

from cognitrix.providers.base import LLM, _apply_provider_param_rules


def _llm(provider, model):
    return LLM(provider=provider, base_url="http://x", api_key="k", model=model)


def _params():
    return {'model': 'm', 'messages': [], 'max_tokens': 8192, 'temperature': 0.4}


@pytest.mark.parametrize('model', ['gpt-5', 'gpt-5.5', 'gpt-5-mini', 'o3', 'o4-mini'])
def test_openai_reasoning_models_drop_temperature_and_max_tokens(model):
    out = _apply_provider_param_rules(_llm('openai', model), _params())
    assert out['max_completion_tokens'] == 8192
    assert 'max_tokens' not in out
    assert 'temperature' not in out


@pytest.mark.parametrize('model', ['gpt-4o', 'gpt-4.1', 'gpt-5-chat-latest'])
def test_openai_non_reasoning_models_keep_temperature(model):
    out = _apply_provider_param_rules(_llm('openai', model), _params())
    assert out['max_completion_tokens'] == 8192
    assert 'max_tokens' not in out
    assert out['temperature'] == 0.4


@pytest.mark.parametrize('provider', ['google', 'groq', 'openrouter'])
def test_other_providers_unchanged(provider):
    out = _apply_provider_param_rules(_llm(provider, 'gpt-5'), _params())
    assert out == _params()


@pytest.mark.parametrize('model', ['claude-opus-4-7', 'claude-opus-4-8', 'claude-sonnet-5', 'claude-sonnet-5-5',
                                   'claude-opus-5-5', 'claude-haiku-5-5', 'claude-fable-5-1', 'anthropic/claude-opus-5'])
def test_new_claude_models_drop_temperature(model):
    out = _apply_provider_param_rules(_llm('anthropic', model), _params())
    assert 'temperature' not in out
    assert out['max_tokens'] == 8192


@pytest.mark.parametrize('model', ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-sonnet-4-5-20250929',
                                   'claude-haiku-4-5-20251001', 'claude-opus-4-20250514', 'claude-3-5-sonnet-latest'])
def test_older_claude_models_keep_temperature(model):
    out = _apply_provider_param_rules(_llm('anthropic', model), _params())
    assert out == _params()


def test_anthropic_drops_json_object_response_format():
    params = {**_params(), 'response_format': {'type': 'json_object'}}
    out = _apply_provider_param_rules(_llm('anthropic', 'claude-sonnet-4-6'), params)
    assert 'response_format' not in out


def test_anthropic_keeps_json_schema_response_format():
    fmt = {'type': 'json_schema', 'json_schema': {'name': 'x', 'strict': True, 'schema': {'type': 'object'}}}
    out = _apply_provider_param_rules(_llm('anthropic', 'claude-sonnet-4-6'), {**_params(), 'response_format': fmt})
    assert out['response_format'] == fmt
