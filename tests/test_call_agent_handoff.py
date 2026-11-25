"""Call Agent can wait for a quick answer (with a time limit) or hand the
job off: the caller carries on and the answer comes back to its chat."""
import asyncio
from types import SimpleNamespace

from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context
from tests.test_session_ownership_routes import _initialize_routes_db, _owned_session


def _fake_turn(monkeypatch, seen, *, delay=0.0, reply='Research done.'):
    from cognitrix.sessions.base import Session

    async def turn(self, message, agent, *args, **kwargs):
        seen.setdefault('turns', []).append((str(self.id), message, kwargs.get('tool_context')))
        output = kwargs.get('output') or (args[2] if len(args) > 2 else None)
        if delay:
            await output({'type': 'chat_reply', 'content': 'Half way.', 'action': 'chat_reply'})
            await asyncio.sleep(delay)
        self.update_history({'role': 'user', 'type': 'text', 'content': message})
        self.update_history({'role': 'assistant', 'type': 'text', 'content': reply})
        await output({'type': 'chat_reply', 'content': reply, 'action': 'chat_reply'})
        if kwargs.get('persist_history', True):
            await self.save()

    monkeypatch.setattr(Session, '__call__', turn)


def _patch_target(monkeypatch):
    target = SimpleNamespace(id='agent-2', name='Researcher', tools=[])

    async def find(_user, **_kwargs):
        return target
    monkeypatch.setattr('cognitrix.agents.access.find_visible_agent', find)
    return target


def _capture_reports(monkeypatch):
    reports = []

    async def deliver(user_key, parent_agent_id, parent_id, text, **kwargs):
        reports.append({'user': user_key, 'agent': parent_agent_id, 'chat': parent_id, 'text': text, **kwargs})
        return 'started'
    monkeypatch.setattr('cognitrix.sessions.agent_chats.deliver_report', deliver)
    return reports


async def _call(context, **kwargs):
    from cognitrix.tools.misc import call_agent

    token = set_execution_context(context)
    try:
        return (await call_agent.run(parent=SimpleNamespace(id='agent-1', name='Planner'), **kwargs)).content
    finally:
        reset_execution_context(token)


async def _background_done():
    from cognitrix.sessions import agent_chats

    for _ in range(50):
        if not agent_chats._TASKS:
            return
        await asyncio.gather(*list(agent_chats._TASKS), return_exceptions=True)


async def test_handoff_returns_at_once_and_reports_back_to_the_callers_chat(tmp_path, monkeypatch):
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.sessions.agent_chats import REPORT_PREFIX
    from cognitrix.tools.misc import HANDOFF_TURN_SECONDS

    await _initialize_routes_db(tmp_path)
    _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen, delay=0.05)
    reports = _capture_reports(monkeypatch)
    parent_chat = await _owned_session('user-1', 'agent-1', chat=[
        {'role': 'user', 'type': 'text', 'content': 'Research three vendors'}])

    out = await _call(
        ToolExecutionContext(session_id=str(parent_chat.id), user_id='user-1', agent_id='agent-1'),
        name='Researcher', task='Compare the vendors', mode='handoff')

    assert out.startswith('Handed off to Researcher (chat id ')
    assert reports == []  # not finished yet: the caller carries on
    helper_id = out.split('chat id ', 1)[1].split(')', 1)[0]
    run = chat_runs.get_run('user-1', helper_id)
    assert run is not None and run.running and run.started_by_agent

    await _background_done()

    assert len(reports) == 1
    report = reports[0]
    assert report['chat'] == str(parent_chat.id)
    assert report['agent'] == 'agent-1'
    assert report['text'].startswith(REPORT_PREFIX)
    assert 'Research done.' in report['text']
    assert helper_id in report['text']
    # The helper keeps a helper's read-only authority in the background.
    _sid, message, tool_context = seen['turns'][0]
    assert message == 'Compare the vendors'
    assert tool_context is not None and tool_context.session_id is None
    assert tool_context.host_process_mode.value == 'deny'
    assert HANDOFF_TURN_SECONDS > 300


async def test_handoff_falls_back_to_waiting_without_a_chat_to_come_back_to(tmp_path, monkeypatch):
    await _initialize_routes_db(tmp_path)
    _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen)
    reports = _capture_reports(monkeypatch)
    secret = await _owned_session('user-1', 'agent-1', incognito=True)

    for context in (
        ToolExecutionContext(user_id='user-1', api_key_id='key-1'),
        ToolExecutionContext(user_id='user-1', session_id=str(secret.id), agent_id='agent-1'),
        ToolExecutionContext(user_id='user-1', agent_id='agent-1'),
    ):
        out = await _call(context, name='Researcher', task='Look it up', mode='handoff')
        assert out.startswith("(Hand-off isn't possible here")
        assert out.endswith('Research done.')
    assert reports == []


async def test_wait_mode_stops_after_its_time_limit(tmp_path, monkeypatch):
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.tools import misc

    await _initialize_routes_db(tmp_path)
    _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen, delay=5)
    monkeypatch.setattr(misc, '_MINUTE', 0.05)
    parent_chat = await _owned_session('user-1', 'agent-1')

    out = await _call(
        ToolExecutionContext(session_id=str(parent_chat.id), user_id='user-1', agent_id='agent-1'),
        name='Researcher', task='A long job', wait_minutes=1)

    assert "did not finish within 1 minutes" in out
    assert "mode='handoff'" in out
    assert 'Half way.' in out
    helper_id = seen['turns'][0][0]
    assert chat_runs.get_run('user-1', helper_id).status == 'stopped'


async def test_wait_is_the_default_and_bad_modes_are_refused(tmp_path, monkeypatch):
    await _initialize_routes_db(tmp_path)
    _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen)
    parent_chat = await _owned_session('user-1', 'agent-1')
    context = ToolExecutionContext(session_id=str(parent_chat.id), user_id='user-1', agent_id='agent-1')

    assert await _call(context, name='Researcher', task='Quick one') == 'Research done.'
    out = await _call(context, name='Researcher', task='Quick one', mode='later')
    assert out.startswith("Error calling agent: mode must be 'wait' or 'handoff'")


def test_call_agent_schema_offers_both_modes():
    from cognitrix.tools.misc import call_agent

    fn = call_agent.to_dict_format()['function']
    assert set(fn['parameters']['properties']) == {'name', 'task', 'mode', 'wait_minutes'}
    assert fn['parameters']['required'] == ['name', 'task']
    assert "'handoff'" in fn['description'] and "'wait'" in fn['description']


def test_wait_stays_under_the_apps_tool_call_limit(monkeypatch):
    from cognitrix.tools import misc

    monkeypatch.setattr('cognitrix.agents.base.tool_timeout_seconds', lambda: 900.0)
    assert misc._wait_seconds(10) == 600
    assert misc._wait_seconds(30) == 885
    monkeypatch.setattr('cognitrix.agents.base.tool_timeout_seconds', lambda: None)
    assert misc._wait_seconds(30) == 1800
