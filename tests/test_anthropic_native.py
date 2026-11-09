"""Claude requests go through Anthropic's Messages API with prompt caching."""
from types import SimpleNamespace as NS

import pytest

from cognitrix.providers import anthropic_native as native
from cognitrix.providers.base import LLM


def _llm(model='claude-sonnet-5-5', **kw):
    return LLM(provider='anthropic', model=model, api_key='k', base_url='https://api.anthropic.com/v1/', **kw)


def test_only_real_anthropic_requests_take_the_native_path(monkeypatch):
    assert native.can_handle(_llm())
    assert not native.can_handle(LLM(provider='openai', model='gpt-5.5', api_key='k'))
    assert not native.can_handle(LLM(provider='anthropic', model='m', api_key='k', base_url='https://proxy.example/v1'))
    assert not native.can_handle(_llm(), response_format={'type': 'json_schema'})
    monkeypatch.setenv('COGNITRIX_ANTHROPIC_NATIVE', '0')
    assert not native.can_handle(_llm())


def test_messages_convert_with_tool_calls_and_results_paired():
    system, messages = native.convert_messages([
        {'role': 'system', 'content': 'Be brief.'},
        {'role': 'user', 'content': 'Weather?'},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'a', 'type': 'function', 'function': {'name': 'w', 'arguments': '{"city": "Accra"}'}},
            {'id': 'b', 'type': 'function', 'function': {'name': 'w', 'arguments': '{"city": "Lagos"}'}},
        ]},
        {'role': 'tool', 'tool_call_id': 'a', 'content': 'Sunny'},
        {'role': 'tool', 'tool_call_id': 'b', 'content': ''},
        {'role': 'system', 'content': 'A note mid-chat.'},
    ])
    assert system == 'Be brief.'
    assert [m['role'] for m in messages] == ['user', 'assistant', 'user']
    assert messages[1]['content'] == [
        {'type': 'tool_use', 'id': 'a', 'name': 'w', 'input': {'city': 'Accra'}},
        {'type': 'tool_use', 'id': 'b', 'name': 'w', 'input': {'city': 'Lagos'}},
    ]
    results = messages[2]['content']
    assert [b['type'] for b in results] == ['tool_result', 'tool_result', 'text']
    assert results[1]['content'] == [{'type': 'text', 'text': '(no output)'}]
    assert results[2]['text'].startswith('[System note]')


def test_unmatched_calls_and_results_are_repaired():
    _, messages = native.convert_messages([
        {'role': 'tool', 'tool_call_id': 'gone', 'content': 'old output'},
        {'role': 'assistant', 'content': 'Checking.', 'tool_calls': [
            {'id': 'x', 'function': {'name': 'w', 'arguments': 'not json'}},
        ]},
        {'role': 'user', 'content': 'Never mind.'},
    ])
    assert messages[0] == {'role': 'user', 'content': [{'type': 'text', 'text': '[Tool result]\nold output'}]}
    assert messages[1]['content'][-1] == {'type': 'tool_use', 'id': 'x', 'name': 'w', 'input': {}}
    assert messages[2]['content'][0]['type'] == 'tool_result'
    assert messages[2]['content'][0]['tool_use_id'] == 'x'
    assert messages[2]['content'][1] == {'type': 'text', 'text': 'Never mind.'}


def test_images_and_trailing_assistant():
    _, messages = native.convert_messages([
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Look'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,QUJD'}},
        ]},
        {'role': 'assistant', 'content': 'Half an answer'},
    ])
    assert messages[0]['content'][1] == {
        'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'QUJD'},
    }
    assert messages[-1]['role'] == 'user'


def test_request_carries_cache_breakpoints_and_no_sampling_params():
    tools = [{'type': 'function', 'function': {'name': 'w', 'description': 'd', 'parameters': {'type': 'object'}}}]
    params = native.build_params(
        _llm(), [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'Hi'}], tools, 'xhigh',
    )
    assert params['cache_control'] == {'type': 'ephemeral'}
    assert params['system'][0]['cache_control'] == {'type': 'ephemeral'}
    assert params['tools'] == [{'name': 'w', 'description': 'd', 'input_schema': {'type': 'object'}}]
    assert params['thinking'] == {'type': 'adaptive', 'display': 'summarized'}
    assert params['output_config'] == {'effort': 'xhigh'}
    assert 'temperature' not in params

    old = native.build_params(_llm('claude-haiku-4-5'), [{'role': 'user', 'content': 'Hi'}], None, 'high')
    assert 'thinking' not in old and 'output_config' not in old and 'system' not in old


def test_context_window_follows_the_model():
    assert _llm('claude-opus-5-5').get_context_window() == 1_000_000
    assert _llm('claude-sonnet-4-6').get_context_window() == 1_000_000
    assert _llm('claude-haiku-4-5').get_context_window() == 200_000
    assert _llm('claude-haiku-4-5', context_window=50_000).get_context_window() == 50_000


class _Events:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()


class _Client:
    def __init__(self, result=None, error=None):
        self.calls = []
        outer = self

        class Messages:
            async def create(self, **params):
                outer.calls.append(params)
                if error:
                    raise error
                return result

        self.messages = Messages()


def _stream_events():
    return _Events([
        NS(type='message_start', message=NS(usage=NS(
            input_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20, output_tokens=1))),
        NS(type='content_block_start', index=0, content_block=NS(type='thinking')),
        NS(type='content_block_delta', index=0, delta=NS(type='thinking_delta', thinking='hmm')),
        NS(type='content_block_start', index=1, content_block=NS(type='text')),
        NS(type='content_block_delta', index=1, delta=NS(type='text_delta', text='Hello')),
        NS(type='content_block_start', index=2, content_block=NS(type='tool_use', id='t1', name='w')),
        NS(type='content_block_delta', index=2, delta=NS(type='input_json_delta', partial_json='{"city":')),
        NS(type='content_block_delta', index=2, delta=NS(type='input_json_delta', partial_json='"Accra"}')),
        NS(type='message_delta', delta=NS(stop_reason='tool_use'), usage=NS(output_tokens=42)),
    ])


@pytest.mark.asyncio
async def test_stream_yields_reasoning_text_tool_calls_and_cache_usage():
    client = _Client(result=_stream_events())
    shown = ''
    last = None
    async for item in native.stream(client, {'model': 'm', 'messages': []}):
        shown += item.current_chunk
        last = item
    assert client.calls[0]['stream'] is True
    assert shown == '<think>hmm\n</think>\nHello'
    assert last.llm_response == 'Hello'
    assert last.reasoning == 'hmm'
    assert last.finish_reason == 'tool_calls'
    assert last.tool_calls == [{'name': 'w', 'arguments': {'city': 'Accra'}, 'tool_call_id': 't1'}]
    assert last.usage == {
        'prompt_tokens': 125, 'completion_tokens': 42, 'cached_tokens': 100, 'cache_write_tokens': 20,
    }


@pytest.mark.asyncio
async def test_non_streaming_reply():
    message = NS(
        content=[NS(type='text', text='Done'), NS(type='tool_use', id='t', name='w', input={'a': 1})],
        stop_reason='end_turn',
        usage=NS(input_tokens=3, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=7),
    )
    reply = await native.respond(_Client(result=message), {'model': 'm', 'messages': []})
    assert reply.llm_response == 'Done' and reply.finish_reason == 'stop'
    assert reply.tool_calls == [{'name': 'w', 'arguments': {'a': 1}, 'tool_call_id': 't'}]
    assert reply.usage['prompt_tokens'] == 3 and reply.usage['completion_tokens'] == 7


@pytest.mark.asyncio
async def test_a_rejected_native_request_falls_back_to_the_compatible_endpoint():
    import anthropic
    import httpx2

    error = anthropic.BadRequestError(
        'nope', response=httpx2.Response(400, request=httpx2.Request('POST', 'https://x')), body=None,
    )

    async def fallback_reply():
        return 'compat reply'

    assert await native.respond(_Client(error=error), {'model': 'm'}, fallback_reply) == 'compat reply'

    async def fallback_stream():
        yield 'compat chunk'

    chunks = [c async for c in native.stream(_Client(error=error), {'model': 'm'}, fallback_stream)]
    assert chunks == ['compat chunk']
