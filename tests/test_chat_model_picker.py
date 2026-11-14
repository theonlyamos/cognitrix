"""Model picked in the chat window for one message (providers.base.turn_llm_override)."""
import pytest

from cognitrix.providers import base


def _llm(**kw):
    data = dict(provider='openai', model='gpt-6.1-sol', api_key='sk-test', base_url='https://api.openai.com/v1')
    data.update(kw)
    return base.LLM(**data)


def test_same_provider_keeps_key_and_endpoint():
    agent_llm = _llm(reasoning_effort='low', context_window=400_000)
    picked = base.build_turn_llm(agent_llm, 'OpenAI', 'gpt-5.5')
    assert picked.model == 'gpt-5.5'
    assert picked.api_key == 'sk-test'
    assert picked.base_url == 'https://api.openai.com/v1'
    assert picked.reasoning_effort == 'low'
    assert picked.context_window == 0
    assert picked.id != agent_llm.id
    assert agent_llm.model == 'gpt-6.1-sol'


def test_other_provider_uses_engine_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')
    picked = base.build_turn_llm(_llm(), 'anthropic', 'claude-sonnet-5-5')
    assert picked.provider == 'anthropic'
    assert picked.model == 'claude-sonnet-5-5'
    assert picked.api_key == 'sk-ant-test-key'


@pytest.mark.parametrize('provider,model', [('claude-code', 'sonnet'), ('nope', 'x'), ('openai', ''), ('openai', 'x' * 201)])
def test_rejects_bad_choices(provider, model):
    with pytest.raises(ValueError):
        base.build_turn_llm(_llm(), provider, model)


def test_harness_agent_cannot_switch():
    agent_llm = base.LLM(provider='claude-code', model='sonnet')
    with pytest.raises(ValueError):
        base.build_turn_llm(agent_llm, 'openai', 'gpt-5.5')


@pytest.mark.asyncio
async def test_only_the_agents_llm_is_redirected(monkeypatch):
    calls = []

    async def fake_generate(llm, prompt, stream, tools, **kw):
        calls.append(llm.model)
        return 'ok'

    async def passthrough(llm, prompt, call, stream=False):
        return await call(llm)

    monkeypatch.setattr(base.LLMManager, 'generate_response', staticmethod(fake_generate))
    import cognitrix.tasks.accounting as accounting
    monkeypatch.setattr(accounting, 'wrap_llm_result', passthrough)

    agent_llm = _llm()
    other = _llm(model='gpt-5.4-mini')
    token = base.turn_llm_override.set((agent_llm.id, base.build_turn_llm(agent_llm, 'openai', 'gpt-5.5')))
    try:
        await agent_llm([{'role': 'user', 'content': 'hi'}])
        await other([{'role': 'user', 'content': 'hi'}])
    finally:
        base.turn_llm_override.reset(token)
    await agent_llm([{'role': 'user', 'content': 'hi'}])
    assert calls == ['gpt-5.5', 'gpt-5.4-mini', 'gpt-6.1-sol']
