"""A person's per-app answers for background computer use."""
import asyncio

import pytest

import cognitrix.computer as computer
from cognitrix.computer import WindowInfo
from cognitrix.computer import apps as computer_apps
from cognitrix.safety.approval_gate import (
    _WEB_PENDING,
    ApprovalGate,
    ToolCall,
    resolve_web_approval,
    web_turn_ctx,
)
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'apps.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setenv('COGNITRIX_APPROVAL_ALERT_DELAY', '3600')
    yield
    _WEB_PENDING.clear()


def _risk():
    return RiskAssessment(RiskLevel.MEDIUM, ['desktop_control'], 'controls the live desktop')


class _Backend:
    def window(self, wid):
        return {7: WindowInfo(7, 'Untitled - Notepad', 'Notepad.exe', 1)}.get(wid)


class _Driver:
    backend = _Backend()


def test_app_of_call(monkeypatch):
    monkeypatch.setattr(computer, 'get_driver', lambda: _Driver())
    assert computer_apps.app_of_call('Click Element', {'window_id': 7, 'element': 1}) == 'notepad.exe'
    assert computer_apps.app_of_call('type_in_window', {'window_id': '7', 'text': 'x'}) == 'notepad.exe'
    # Looking isn't an action; taking the screen is never covered by an app answer.
    assert computer_apps.app_of_call('look_at_window', {'window_id': 7}) is None
    assert computer_apps.app_of_call('type_in_window', {'window_id': 7, 'use_screen': True}) is None
    assert computer_apps.app_of_call('click_element', {'window_id': 99}) is None
    assert computer_apps.app_of_call('click_element', {}) is None
    assert computer_apps.app_label('notepad.exe') == 'Notepad'


async def test_levels_are_saved_per_person(db):
    assert await computer_apps.level('u1', 'notepad.exe') == 'ask'
    await computer_apps.set_level('u1', 'Notepad.exe', 'allow')
    await computer_apps.set_level('u1', 'outlook.exe', 'deny')
    assert await computer_apps.level('u1', 'notepad.exe') == 'allow'
    assert await computer_apps.level('u1', 'outlook.exe') == 'deny'
    assert await computer_apps.level('u2', 'notepad.exe') == 'ask'
    assert await computer_apps.level(None, 'notepad.exe') == 'ask'  # the CLI has its own
    assert [r['app'] for r in await computer_apps.list_levels('u1')] == ['notepad.exe', 'outlook.exe']
    await computer_apps.set_level('u1', 'notepad.exe', 'ask')
    assert await computer_apps.level('u1', 'notepad.exe') == 'ask'
    with pytest.raises(ValueError):
        await computer_apps.set_level('u1', 'notepad.exe', 'sometimes')


async def _ask(gate, call, user='u1', session='s1'):
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def run():
        token = web_turn_ctx.set({'emit': emit, 'session_id': session, 'bypass': False, 'user_key': user})
        try:
            return await gate.check_approval(call, _risk(), interface='web', timeout=5, scope='agent-1')
        finally:
            web_turn_ctx.reset(token)

    task = asyncio.create_task(run())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if emitted or task.done():
            break
    return (emitted[0] if emitted else None), task


async def test_allow_app_in_this_chat(db):
    gate = ApprovalGate(cache_dir=None)
    gate.permanent_cache.clear()
    event, task = await _ask(gate, ToolCall('click_element', {'window_id': 7, 'element': 1}, app='notepad.exe'))
    assert event['app'] == {'key': 'notepad.exe', 'label': 'Notepad'}
    assert resolve_web_approval(event['request_id'], approved=True, user_key='u1', remember_app='chat')
    assert (await task).approved
    # A different action in the same app and chat no longer asks.
    event, task = await _ask(gate, ToolCall('set_text', {'window_id': 7, 'text': 'hi'}, app='notepad.exe'))
    assert event is None and (await task).cached
    # Another app, another chat and another person still ask.
    for call, kw in (
        (ToolCall('set_text', {'window_id': 8, 'text': 'hi'}, app='outlook.exe'), {}),
        (ToolCall('set_text', {'window_id': 7, 'text': 'hi'}, app='notepad.exe'), {'session': 's2'}),
        (ToolCall('set_text', {'window_id': 7, 'text': 'hi'}, app='notepad.exe'), {'user': 'u2'}),
    ):
        event, task = await _ask(gate, call, **kw)
        assert event is not None
        resolve_web_approval(event['request_id'], approved=False)
        assert not (await task).approved
    assert await computer_apps.level('u1', 'notepad.exe') == 'ask'  # nothing was saved


async def test_always_allow_app_is_saved(db):
    gate = ApprovalGate(cache_dir=None)
    gate.permanent_cache.clear()
    event, task = await _ask(gate, ToolCall('click_element', {'window_id': 7, 'element': 1}, app='notepad.exe'))
    resolve_web_approval(event['request_id'], approved=True, user_key='u1', remember_app='always')
    assert (await task).approved
    assert await computer_apps.level('u1', 'notepad.exe') == 'allow'
    assert await computer_apps.level('u2', 'notepad.exe') == 'ask'


async def test_remember_app_needs_an_app_and_an_approval(db):
    gate = ApprovalGate(cache_dir=None)
    gate.permanent_cache.clear()
    # Not an app action: "always" is ignored.
    event, task = await _ask(gate, ToolCall('Delete File', {'path': 'a.txt'}))
    assert 'app' not in event
    resolve_web_approval(event['request_id'], approved=True, user_key='u1', remember_app='always')
    assert (await task).remember_app is None
    # A denial saves nothing.
    event, task = await _ask(gate, ToolCall('click_element', {'window_id': 7, 'element': 2}, app='notepad.exe'))
    resolve_web_approval(event['request_id'], approved=False, user_key='u1', remember_app='always')
    assert not (await task).approved
    assert await computer_apps.level('u1', 'notepad.exe') == 'ask'


async def test_a_screen_takeover_is_asked_every_time(monkeypatch):
    """No remembered answer or chat auto-approve stands in for the person."""
    from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall, web_turn_ctx
    from cognitrix.safety.destructive_ops import DestructiveOpDetector

    call = ToolCall('type_in_window', {'window_id': 1, 'text': 'hi', 'use_screen': True})
    risk = DestructiveOpDetector().analyze(call.tool_name, call.params)
    assert 'screen_takeover' in risk.categories

    gate = ApprovalGate()
    asked = []

    async def cli(tool_call, risk, timeout):
        asked.append(tool_call)
        return ApprovalResult(approved=True, remember=True, permanent=True)

    monkeypatch.setattr(gate, '_cli_approval', cli)
    monkeypatch.setattr(gate, '_save_permanent_cache', lambda: None)
    for _ in range(2):
        assert (await gate.check_approval(call, risk, interface='cli', scope='a1')).approved
    assert len(asked) == 2 and not gate.session_cache and not gate.permanent_cache

    # Auto-approve in the browser doesn't answer it either: with nobody to ask it is refused.
    token = web_turn_ctx.set({'bypass': True, 'session_id': 's1', 'user_key': 'u1'})
    try:
        result = await gate.check_approval(call, risk, interface='web', scope='a1')
    finally:
        web_turn_ctx.reset(token)
    assert not result.approved and not result.auto


class _OwnBackend:
    def window(self, wid):
        return {5: WindowInfo(5, 'Cognitrix', 'cognitrix.exe', 2, own=True),
                7: WindowInfo(7, 'Untitled - Notepad', 'Notepad.exe', 1)}.get(wid)


class _OwnDriver:
    backend = _OwnBackend()


def test_actions_in_cognitrix_itself_are_marked_own_app(monkeypatch):
    from cognitrix.safety.destructive_ops import DestructiveOpDetector

    monkeypatch.setattr(computer, 'get_driver', lambda: _OwnDriver())
    detector = DestructiveOpDetector()
    own = detector.analyze('click_element', {'window_id': 5, 'element': 1, 'computer': 'person'})
    assert own.risk_level is RiskLevel.MEDIUM and 'own_app' in own.categories
    other = detector.analyze('click_element', {'window_id': 7, 'element': 1, 'computer': 'person'})
    assert 'own_app' not in other.categories
    # With the screen too, so no reviewer answers it in a task.
    screen = detector.analyze('type_in_window', {'window_id': 5, 'text': 'x', 'use_screen': True,
                                                 'computer': 'person'})
    assert {'screen_takeover', 'own_app'} <= set(screen.categories)
    assert computer_apps.app_of_call('type_in_window', {'window_id': 5, 'use_screen': True}) is None
    assert computer.is_own_app('Cognitrix-Desktop.exe') and not computer.is_own_app('notepad.exe')


async def test_cognitrix_itself_needs_the_persons_answer(monkeypatch):
    """Chat auto-approve, the reviewer and "always allow this tool" don't
    cover actions in Cognitrix itself; the person's answer for the app does."""
    gate = ApprovalGate()
    call = ToolCall('click_element', {'window_id': 5, 'element': 1}, app='cognitrix.exe')
    risk = RiskAssessment(RiskLevel.MEDIUM, ['desktop_control', 'own_app'], 'controls Cognitrix itself')

    async def review(*_a, **_k):
        raise AssertionError('the reviewer must not answer for the person')

    monkeypatch.setattr(gate, '_review', review)
    gate.session_cache.add(gate._tool_allow_key('a1', call))
    token = web_turn_ctx.set({'bypass': True, 'session_id': 's1', 'user_key': 'u1'})
    try:
        result = await gate.check_approval(call, risk, interface='web', scope='a1', timeout=0.05)
        assert not result.approved and not result.auto
        # The person allowed Cognitrix for this chat: that answer counts.
        gate.session_cache.add(gate._app_key(call, 'web'))
        assert (await gate.check_approval(call, risk, interface='web', scope='a1')).approved
    finally:
        web_turn_ctx.reset(token)
        _WEB_PENDING.clear()


async def test_waiting_task_approvals_are_found(db):
    from datetime import datetime, timedelta

    from cognitrix.tasks.approvals import APPROVED, TaskApproval, any_waiting

    assert not await any_waiting()
    await TaskApproval(run_id='r1', user_id='u1', tool_name='Write', status=APPROVED).save()
    assert not await any_waiting()
    old = TaskApproval(run_id='r2', user_id='u1', tool_name='Write',
                       created_at=datetime.now() - timedelta(days=2))
    await old.save()
    assert not await any_waiting()  # long expired, not a card anyone sees
    await TaskApproval(run_id='r3', user_id='u1', tool_name='Write').save()
    assert await any_waiting()


async def test_task_approvals_are_checked_again_right_before_acting(db):
    """The driver runs in a worker thread; it asks the main loop's database."""
    from cognitrix.tasks.approvals import TaskApproval

    computer.remember_loop(asyncio.get_running_loop())
    try:
        assert await asyncio.to_thread(computer.own_app_busy) is None
        await TaskApproval(run_id='r9', user_id='u1', tool_name='Write').save()
        assert await asyncio.to_thread(computer.own_app_busy) == computer.OWN_APP_BUSY
    finally:
        computer.remember_loop(None)
