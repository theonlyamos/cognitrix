"""OpenAI models that refuse function tools while reasoning on /chat/completions."""
import httpx
import openai
import pytest

from cognitrix.providers import base


def _bad_request(message: str) -> openai.BadRequestError:
    request = httpx.Request('POST', 'https://api.openai.com/v1/chat/completions')
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(message, response=response, body=None)


class _Completions:
    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    async def create(self, **params):
        self.calls.append(params)
        if self.fail_with and 'reasoning_effort' not in params:
            raise self.fail_with
        return 'ok'


class _Client:
    def __init__(self, completions):
        self.chat = type('Chat', (), {'completions': completions})()


REFUSAL = ("Error code: 400 - Function tools with reasoning_effort are not supported for gpt-6.1-sol "
           "in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.")


@pytest.mark.asyncio
async def test_retries_with_reasoning_off_and_remembers_the_model(monkeypatch):
    monkeypatch.setattr(base, '_NO_REASONING_WITH_TOOLS', set())
    completions = _Completions(fail_with=_bad_request(REFUSAL))
    params = {'model': 'gpt-6.1-sol', 'messages': [], 'tools': [{'type': 'function'}]}

    assert await base._create_with_retry(_Client(completions), params) == 'ok'
    assert [c.get('reasoning_effort') for c in completions.calls] == [None, 'none']

    # The next call goes straight to reasoning_effort='none'.
    again = _Completions(fail_with=_bad_request(REFUSAL))
    assert await base._create_with_retry(_Client(again), params) == 'ok'
    assert [c.get('reasoning_effort') for c in again.calls] == ['none']


@pytest.mark.asyncio
async def test_other_bad_requests_and_chats_without_tools_still_fail(monkeypatch):
    monkeypatch.setattr(base, '_NO_REASONING_WITH_TOOLS', set())
    with pytest.raises(openai.BadRequestError):
        await base._create_with_retry(_Client(_Completions(fail_with=_bad_request('Unknown parameter'))),
                                      {'model': 'gpt-6.1-sol', 'messages': [], 'tools': [{}]})
    with pytest.raises(openai.BadRequestError):
        await base._create_with_retry(_Client(_Completions(fail_with=_bad_request(REFUSAL))),
                                      {'model': 'gpt-6.1-sol', 'messages': []})
    # A reasoning_effort the agent chose itself is not overridden.
    with pytest.raises(openai.BadRequestError):
        await base._create_with_retry(_Client(_Completions(fail_with=_bad_request(REFUSAL))),
                                      {'model': 'gpt-6.1-sol', 'messages': [], 'tools': [{}],
                                       'extra_body': {'reasoning_effort': 'high'}})


def test_gpt6_counts_as_a_reasoning_model():
    assert base._is_openai_reasoning_model('gpt-6.1-sol')
    assert base._is_openai_reasoning_model('gpt-5.4')
    assert not base._is_openai_reasoning_model('gpt-4.1')


@pytest.mark.asyncio
async def test_agent_reasoning_level_falls_back_to_none_when_tools_are_refused(monkeypatch):
    monkeypatch.setattr(base, '_NO_REASONING_WITH_TOOLS', set())
    completions = _Completions(fail_with=_bad_request(REFUSAL))

    async def create(**params):
        completions.calls.append(params)
        if params.get('reasoning_effort') != 'none':
            raise _bad_request(REFUSAL)
        return 'ok'

    completions.create = create
    params = {'model': 'gpt-6.1-sol', 'messages': [], 'tools': [{}], 'reasoning_effort': 'high'}
    assert await base._create_with_retry(_Client(completions), params) == 'ok'
    assert [c['reasoning_effort'] for c in completions.calls] == ['high', 'none']


@pytest.mark.asyncio
async def test_reasoning_level_is_sent_and_the_chat_choice_wins(monkeypatch):
    from cognitrix.config import settings

    seen = []

    async def capture(_client, params):
        seen.append(params)
        return 'done'

    monkeypatch.setattr(settings, 'has_api_key', lambda name: False)
    monkeypatch.setattr(base, 'assert_key_destination_allowed', lambda *a, **k: None)
    monkeypatch.setattr(base, '_get_or_create_client', lambda *a, **k: object())
    monkeypatch.setattr(base.LLMManager, '_handle_non_streaming_response', staticmethod(capture))

    llm = base.LLM(provider='openai', model='gpt-6.1-sol', api_key='k', base_url='https://api.openai.com/v1',
                   reasoning_effort='low')
    await base.LLMManager.generate_response(llm, [{'role': 'user', 'content': 'hi'}])
    token = base.turn_reasoning_effort.set('high')
    try:
        await base.LLMManager.generate_response(llm, [{'role': 'user', 'content': 'hi'}])
    finally:
        base.turn_reasoning_effort.reset(token)
    llm.reasoning_effort = ''
    await base.LLMManager.generate_response(llm, [{'role': 'user', 'content': 'hi'}])

    assert [p.get('reasoning_effort') for p in seen] == ['low', 'high', None]
    assert base.normalize_reasoning_effort('HIGH ') == 'high'
    assert base.normalize_reasoning_effort('turbo') == ''
