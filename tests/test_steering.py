"""Messages sent while a chat turn runs are read between tool rounds."""

import types

import pytest

from cognitrix.models.agent import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM, STEER_NOTE, LLMManager
from cognitrix.sessions import runs as chat_runs
from cognitrix.sessions.base import Session
from cognitrix.sessions.context import partition_turns, shape_history
from cognitrix.tools.resilient_tool_wrapper import ToolResult
from cognitrix.utils.llm_response import LLMResponse


@pytest.fixture(autouse=True)
def _clean_runs():
    chat_runs._RUNS.clear()
    yield
    chat_runs._RUNS.clear()


def _agent(tools):
    return Agent(
        name='Assistant',
        llm=LLM(provider='openai', base_url='http://example.invalid', api_key='test', model='test'),
        system_prompt='Test',
        tools=tools,
    )


def _patch(monkeypatch, replies, on_tool=None):
    """Fake model: pops one reply per call ('tool' or text); records prompts."""
    prompts = []
    probe = Tool(name='probe', description='Probe', parameters={})

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        prompts.append(prompt)
        response = LLMResponse()
        kind = replies.pop(0)
        if callable(kind):
            kind = kind()
        if kind == 'tool':
            response.tool_calls = [{'name': 'probe', 'arguments': {}, 'tool_call_id': f'c{len(prompts)}'}]
        else:
            response.add_chunk(kind)
        return response

    async def fake_run_tool(self, tool, params, **kwargs):
        if on_tool:
            on_tool()
        return ToolResult(success=True, data='probed')

    async def fake_save(self):
        return None

    monkeypatch.setattr('cognitrix.providers.base.LLMManager.generate_response', staticmethod(fake_generate))
    monkeypatch.setattr('cognitrix.agents.base.ToolManager.get_by_name', staticmethod(lambda name: probe))
    monkeypatch.setattr('cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool', fake_run_tool)
    monkeypatch.setattr(Session, 'save', fake_save)
    return probe, prompts


async def _run_turn(session, agent, run):
    events = []

    async def sink(payload=None, *args, **kwargs):
        if isinstance(payload, dict):
            events.append(payload)

    run.steerable = True
    token = chat_runs.current_run.set(run)
    try:
        await session('start the job', agent, 'web', False, sink, {'type': 'generate', 'action': 'chat_message'}, True)
    finally:
        chat_runs.current_run.reset(token)
    return events


def _flat(prompt):
    llm = types.SimpleNamespace(provider='openai', is_multimodal=False, model='m')
    return str(LLMManager.format_query(llm, prompt))


@pytest.mark.asyncio
async def test_message_sent_during_a_tool_call_is_read_in_the_same_turn(monkeypatch):
    session = Session(agent_id='a1')
    session.id = 's1'
    run = chat_runs.start_run('u', 'a1', 's1', prompt='start the job')
    probe, prompts = _patch(
        monkeypatch, ['tool', 'done, used the blue one'],
        on_tool=lambda: run.steer('use the blue one instead'),
    )
    agent = _agent([probe])

    events = await _run_turn(session, agent, run)

    assert len(prompts) == 2
    assert 'use the blue one instead' not in _flat(prompts[0])
    assert STEER_NOTE.strip() in _flat(prompts[1])
    assert 'use the blue one instead' in _flat(prompts[1])
    # In history: after the tool result, before the final answer.
    kinds = [(m.get('role'), m.get('type')) for m in session.chat]
    steer_at = kinds.index(('User', 'steer'))
    assert kinds[steer_at - 1][0] == 'tool'
    assert session.chat[-2]['content'] == 'done, used the blue one' or any(
        m.get('content') == 'done, used the blue one' for m in session.chat[steer_at:]
    )
    delivered = [e for e in events if e.get('type') == 'message_delivered']
    assert delivered and delivered[0]['messages'][0]['content'] == 'use the blue one instead'
    assert run.inbox == []


@pytest.mark.asyncio
async def test_message_arriving_with_the_final_answer_keeps_the_turn_going(monkeypatch):
    session = Session(agent_id='a1')
    session.id = 's1'
    run = chat_runs.start_run('u', 'a1', 's1')

    def answer_then_steer():
        run.steer('also check the logs')
        return 'first answer'

    probe, prompts = _patch(monkeypatch, [answer_then_steer, 'logs checked'])
    await _run_turn(session, _agent([probe]), run)

    assert len(prompts) == 2
    assert 'also check the logs' in _flat(prompts[1])
    texts = [m.get('content') for m in session.chat if m.get('role') == 'assistant']
    assert texts == ['first answer', 'logs checked']


@pytest.mark.asyncio
async def test_nested_session_does_not_read_another_chats_inbox(monkeypatch):
    other = Session(agent_id='a1')
    other.id = 'other'
    run = chat_runs.start_run('u', 'a1', 's1')
    run.steer('for s1 only')
    probe, prompts = _patch(monkeypatch, ['ok'])
    await _run_turn(other, _agent([probe]), run)
    assert len(prompts) == 1
    assert [m.content for m in run.inbox] == ['for s1 only']


def test_unread_messages_come_back_on_the_terminal_event():
    run = chat_runs.start_run('u', 'a1', 's1')
    first = run.steer('one')
    run.steer('two')
    assert run.withdraw(first.id).content == 'one'
    assert run.withdraw(first.id) is None
    run.finish({'type': 'turn_stopped', 'content': '', 'session_id': 's1'})
    assert [m['content'] for m in run.unsent] == ['two']
    assert run.events[-1]['unsent'][0]['content'] == 'two'
    # Once ended, nothing more is accepted: the caller starts a normal turn.
    assert run.steer('three') is None


def test_inbox_is_bounded_and_rejects_empty_messages():
    run = chat_runs.start_run('u', 'a1', 's1')
    with pytest.raises(ValueError):
        run.steer('   ')
    for i in range(chat_runs.MAX_INBOX):
        run.steer(f'm{i}')
    with pytest.raises(chat_runs.InboxFull):
        run.steer('one too many')


def test_module_steer_needs_a_run_that_reads_its_inbox():
    run = chat_runs.start_run('u', 'a1', 's1')
    assert chat_runs.steer('u', 's1', 'hi') is None
    run.steerable = True
    assert chat_runs.steer('u', 's1', 'hi').content == 'hi'
    assert chat_runs.steer('u', 'missing', 'hi') is None


def test_steer_message_stays_in_the_running_turn_and_in_past_turns():
    chat = [
        {'role': 'User', 'type': 'text', 'content': 'do it'},
        {'role': 'assistant', 'type': 'tool_calls', 'content': '', 'tool_calls': [{'name': 'x', 'tool_call_id': 't1'}]},
        {'role': 'tool', 'type': 'text', 'content': 'r', 'tool_call_id': 't1'},
        {'role': 'User', 'type': 'steer', 'content': 'change of plan'},
        {'role': 'assistant', 'type': 'text', 'content': 'ok'},
    ]
    assert len(partition_turns(chat)) == 1
    later = [*chat, {'role': 'User', 'type': 'text', 'content': 'next'}]
    for i in range(5):  # push the first turn out of the recent-tools window
        later += [{'role': 'assistant', 'type': 'text', 'content': f'a{i}'},
                  {'role': 'User', 'type': 'text', 'content': f'q{i}'}]
    shaped = shape_history(later, 100_000, max_past_turns=20)
    assert any(m.get('content') == 'change of plan' for m in shaped)


def test_formatter_marks_steer_messages():
    llm = types.SimpleNamespace(provider='openai', is_multimodal=False, model='m')
    out = LLMManager.format_query(llm, [{'role': 'User', 'type': 'steer', 'content': 'stop using X'}])
    assert out == [{'role': 'user', 'content': STEER_NOTE + 'stop using X'}]


class _JsonRequest:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    async def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_chat_endpoint_steers_a_running_chat_and_can_take_it_back(monkeypatch):
    import cognitrix.api.routes.agents as agent_routes
    from cognitrix.utils.sse import SSEManager

    agent = types.SimpleNamespace(id='agent-1')
    manager = SSEManager(agent)

    async def resolve_agent(_agent_id, _request, _user=None):
        return agent

    monkeypatch.setattr(agent_routes, '_resolve_agent', resolve_agent)
    monkeypatch.setattr(agent_routes, 'get_sse_manager', lambda *a, **k: manager)
    user = types.SimpleNamespace(id='user-a')
    key = agent_routes._user_key(user)
    run = chat_runs.start_run(key, 'agent-1', 's1')
    run.steerable = True

    result = await agent_routes.chat_endpoint(
        _JsonRequest({'agent_id': 'agent-1', 'session_id': 's1', 'message': 'use staging'}), user=user)
    assert result['status'] == 'queued'
    assert result['run_id'] == run.run_id
    assert [m.content for m in run.inbox] == ['use staging']
    assert manager.turn_pending is False  # no new turn was started

    taken = await agent_routes.withdraw_chat_message('s1', result['message']['id'], user=user)
    assert taken['message']['content'] == 'use staging'
    with pytest.raises(agent_routes.HTTPException) as exc:
        await agent_routes.withdraw_chat_message('s1', result['message']['id'], user=user)
    assert exc.value.status_code == 409

    # A message meant for an earlier turn is not slipped into this one.
    with pytest.raises(agent_routes.HTTPException) as exc:
        await agent_routes.chat_endpoint(_JsonRequest({
            'agent_id': 'agent-1', 'session_id': 's1', 'message': 'x', 'expected_run_id': 'old',
        }), user=user)
    assert exc.value.status_code == 409

    # Task mode still waits.
    with pytest.raises(agent_routes.HTTPException) as exc:
        await agent_routes.chat_endpoint(_JsonRequest({
            'agent_id': 'agent-1', 'session_id': 's1', 'message': 'x', 'execution_mode': 'task',
        }), user=user)
    assert exc.value.status_code == 409


def test_runs_that_follow_up_themselves_keep_unread_messages_off_the_event():
    run = chat_runs.start_run('u', 'a1', 's-follow')
    run.follows_up = True
    run.steer('from the app', source='telegram')
    run.finish({'type': 'turn_complete', 'content': '', 'session_id': 's-follow'})
    assert [m['content'] for m in run.unsent] == ['from the app']
    assert 'unsent' not in run.events[-1]

    stopped = chat_runs.start_run('u', 'a1', 's-follow-2')
    stopped.follows_up = True
    stopped.steer('typed on the page', source='web')
    stopped.steer('from the app', source='telegram')
    stopped.finish({'type': 'turn_stopped', 'content': '', 'session_id': 's-follow-2'})
    assert [m['content'] for m in stopped.events[-1]['unsent']] == ['typed on the page']
