"""Gemini-compat provider shims.

Gemini's OpenAI-compat endpoint streams tool calls with index=None (one delta
per call, keyed by id) and attaches a thought_signature under extra_content
that must be echoed back on the re-prompt — but only to Gemini; other
providers can reject unknown fields in tool_calls.
"""

import types

import pytest

from cognitrix.providers.base import LLM, LLMManager

SIG = {'google': {'thought_signature': 'sig'}}


def _llm(provider):
    return LLM(provider=provider, base_url="http://x", api_key="k", model="m")


def _tc(name=None, args=None, tc_id=None, index=None, extra=None):
    fn = types.SimpleNamespace(name=name, arguments=args)
    return types.SimpleNamespace(index=index, id=tc_id, function=fn, extra_content=extra)


def _chunk(tool_calls=None, content=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])


class _FakeClient:
    def __init__(self, chunks):
        completions = types.SimpleNamespace(create=lambda **params: iter(chunks))
        self.chat = types.SimpleNamespace(completions=completions)


async def _drain(chunks):
    last = None
    async for r in LLMManager._handle_streaming_response(_FakeClient(chunks), {}):
        last = r
    return last


@pytest.mark.asyncio
async def test_stream_tool_call_without_index_is_kept():
    # Gemini sends the whole call in one delta with index=None — it must not
    # be dropped, and extra_content must survive.
    last = await _drain([
        _chunk(tool_calls=[_tc('Read', '{"file_path": "x.txt"}', tc_id='a', extra=SIG)]),
    ])
    assert last.tool_calls == [{
        'name': 'Read', 'arguments': {'file_path': 'x.txt'},
        'tool_call_id': 'a', 'extra_content': SIG,
    }]
    # Final yield is finalization-only; must not re-emit the last text chunk.
    assert last.current_chunk == ''


@pytest.mark.asyncio
async def test_stream_multiple_unindexed_calls_stay_separate():
    last = await _drain([
        _chunk(tool_calls=[_tc('t1', '{}', tc_id='a')]),
        _chunk(tool_calls=[_tc('t2', '{}', tc_id='b')]),
    ])
    assert [tc['name'] for tc in last.tool_calls] == ['t1', 't2']
    assert [tc['tool_call_id'] for tc in last.tool_calls] == ['a', 'b']


@pytest.mark.asyncio
async def test_stream_unindexed_idless_delta_continues_last_call():
    # A delta with neither index nor id is an argument continuation.
    last = await _drain([
        _chunk(tool_calls=[_tc('t1', '{"x": ', tc_id='a')]),
        _chunk(tool_calls=[_tc(None, '1}')]),
    ])
    assert last.tool_calls == [{'name': 't1', 'arguments': {'x': 1}, 'tool_call_id': 'a'}]


def test_format_query_echoes_extra_content_only_to_gemini():
    chat = [{
        'role': 'assistant', 'type': 'tool_calls', 'content': '',
        'tool_calls': [{'name': 'Read', 'arguments': {}, 'tool_call_id': '1', 'extra_content': SIG}],
    }]
    google_tc = LLMManager.format_query(_llm('google'), chat)[0]['tool_calls'][0]
    openai_tc = LLMManager.format_query(_llm('openai'), chat)[0]['tool_calls'][0]
    assert google_tc['extra_content'] == SIG
    assert 'extra_content' not in openai_tc
