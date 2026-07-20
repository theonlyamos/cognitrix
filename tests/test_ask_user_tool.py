import asyncio

import pytest

from cognitrix.agents.base import AgentManager
from cognitrix.models.agent import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM
from cognitrix.sessions.base import Session
from cognitrix.questions.broker import (
    QuestionTurnContext,
    question_turn_ctx,
    resolve_question,
)
from cognitrix.tools.base import ToolManager, clear_tool_cache
from cognitrix.tools.resilient_tool_wrapper import ToolResult
from cognitrix.utils.llm_response import LLMResponse


def _agent(tools: list[Tool]) -> Agent:
    return Agent(
        name='Assistant',
        llm=LLM(
            provider='openai', base_url='http://example.invalid',
            api_key='test', model='test',
        ),
        system_prompt='Test',
        tools=tools,
    )


def test_ask_user_is_a_web_only_non_slot_tool():
    clear_tool_cache()
    ask_user = ToolManager.get_by_name('ask_user')

    assert ask_user is not None
    assert ask_user.supported_interfaces == ['web']
    assert ask_user.occupies_execution_slot is False
    assert ask_user.retryable is False
    assert ask_user.max_attempts == 1
    assert ask_user.approval_mode == 'assigned_only'


@pytest.mark.asyncio
async def test_ask_user_emits_and_returns_the_selected_answer():
    clear_tool_cache()
    ask_user = ToolManager.get_by_name('ask_user')
    events = []

    async def emit(event):
        events.append(event)

    token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit,
        session_id='session-1',
        stream_id='stream-1',
        user_key='user-1',
    ))
    try:
        running = asyncio.create_task(ask_user.run(
            prompt='How should I continue?',
            options=[
                {'id': 'a', 'label': 'Continue'},
                {'id': 'b', 'label': 'Stop'},
            ],
            recommended_option_id='a',
        ))
        await asyncio.sleep(0)

        assert events[0]['type'] == 'question_request'
        assert await resolve_question(
            events[0]['request_id'], 'user-1', 'answer', option_id='a',
        )
        result = await running
    finally:
        question_turn_ctx.reset(token)

    assert 'Continue' in str(result)
    assert '"option_id": "a"' in str(result)


@pytest.mark.asyncio
async def test_waiting_question_does_not_block_an_ordinary_tool(monkeypatch):
    waiters = [
        Tool(
            name=f'wait {index}',
            description='Wait for input',
            parameters={},
            approval_mode='assigned_only',
            occupies_execution_slot=False,
        )
        for index in range(4)
    ]
    ordinary = Tool(
        name='ordinary',
        description='Complete immediately',
        parameters={},
        approval_mode='assigned_only',
    )
    tools = [*waiters, ordinary]
    release = asyncio.Event()
    ordinary_ran = asyncio.Event()

    monkeypatch.setattr(
        'cognitrix.agents.base.ToolManager.get_by_name',
        staticmethod(lambda name: next(
            tool for tool in tools
            if tool.name.casefold().replace(' ', '_') == name.casefold().replace(' ', '_')
        )),
    )

    async def fake_run_tool(self, tool, params, **kwargs):
        if tool.name.startswith('wait'):
            await release.wait()
        else:
            ordinary_ran.set()
        return ToolResult(success=True, data=tool.name)

    monkeypatch.setattr(
        'cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool',
        fake_run_tool,
    )

    calls = [
        {'name': tool.name.replace(' ', '_'), 'arguments': {}, 'tool_call_id': str(index)}
        for index, tool in enumerate(tools)
    ]
    running = asyncio.create_task(AgentManager(_agent(tools)).call_tools(calls, interface='web'))
    try:
        await asyncio.wait_for(ordinary_ran.wait(), timeout=0.25)
    finally:
        release.set()
    result = await running

    assert len(result['result']) == 5


@pytest.mark.asyncio
async def test_session_advertises_ask_user_only_to_direct_web_chat(monkeypatch):
    ordinary = Tool(name='ordinary', description='Ordinary', parameters={})
    agent = _agent([ordinary])
    session = Session(agent_id=str(agent.id))
    advertised = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        advertised.append([
            item['function']['name'] for item in (tools or [])
        ])
        response = LLMResponse()
        response.add_chunk('done')
        return response

    async def fake_save(self):
        return None

    async def sink(*args, **kwargs):
        return None

    monkeypatch.setattr(
        'cognitrix.providers.base.LLMManager.generate_response',
        staticmethod(fake_generate),
    )
    monkeypatch.setattr(Session, 'save', fake_save)

    await session('web turn', agent, 'web', False, sink, None, True)
    await session('cli turn', agent, 'cli', False, lambda *args, **kwargs: None, None, True)

    assert 'Ask_User' in advertised[0]
    assert 'Ask_User' not in advertised[1]
