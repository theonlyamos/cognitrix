"""Always-on agents: notes, agent wake-ups, webhook triggers, notifications."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException

from cognitrix.common.security import AuthContext
from cognitrix.tasks.automation import (
    DISPATCHED,
    DROPPED,
    PENDING,
    AgentNotification,
    TaskTrigger,
    TriggerEvent,
    automation_brief,
    dispatch_trigger_events,
    fire_due_wakes,
    new_trigger_token,
)
from cognitrix.tasks.base import Task
from cognitrix.tasks.run import TaskRun, TaskRunStatus
from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

NOW = datetime(2030, 6, 1, 12, 0, 0)


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.run import TaskRunHead

    db_file = str(tmp_path / 'automation.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (Task, TaskRun, TaskRunHead, TaskTrigger, TriggerEvent, AgentNotification):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()


@pytest.fixture
def enqueue(monkeypatch):
    import cognitrix.api.routes.tasks as task_routes

    calls: list = []
    script: list = []

    async def fake_enqueue(task, resume=False, **kwargs):
        calls.append((task.id, kwargs))
        if script:
            raise script.pop(0)
        return SimpleNamespace(id=f'run-{len(calls)}')

    monkeypatch.setattr(task_routes, '_enqueue_task_start', fake_enqueue)
    fake_enqueue.calls = calls
    fake_enqueue.script = script
    return fake_enqueue


def _ctx(user='u1'):
    return AuthContext(user=SimpleNamespace(id=user), api_key=None)


async def _task(**kw):
    kw.setdefault('owner_id', 'u1')
    kw.setdefault('assigned_agents', ['a1'])
    task = Task(title='watch', description='watch the thing', **kw)
    await task.save()
    return task


async def _run(task, **kw):
    run = TaskRun(task_id=task.id, status=TaskRunStatus.RUNNING, requested_by='u1',
                  authority_kind='jwt', authority_id='u1', **kw)
    await run.save()
    return run


@pytest.fixture
def in_run():
    tokens = []

    def bind(task, run):
        tokens.append(set_execution_context(ToolExecutionContext(
            user_id='u1', task_id=str(task.id), run_id=str(run.id))))

    yield bind
    set_execution_context(ToolExecutionContext())


async def _call(tool, **params):
    result = await tool.run(**params)
    return result.outcome


# --- tools ---------------------------------------------------------------------

async def test_notes_tools_replace_append_and_limit(db, in_run):
    from cognitrix.tools.automation import read_notes, update_notes

    task = await _task()
    in_run(task, await _run(task))
    assert (await _call(read_notes)).text == '(no notes yet)'
    assert (await _call(update_notes, content='v1.2 seen')).status == 'success'
    assert (await _call(update_notes, content='v1.3 seen', mode='append')).status == 'success'
    assert (await Task.get(task.id)).notes == 'v1.2 seen\nv1.3 seen'
    assert (await _call(read_notes)).text == 'v1.2 seen\nv1.3 seen'

    too_long = await _call(update_notes, content='x' * 9000)
    assert too_long.status == 'error' and too_long.error.code == 'notes_too_long'
    bad_mode = await _call(update_notes, content='x', mode='merge')
    assert bad_mode.status == 'error'


async def test_tools_refuse_outside_a_task_run(db):
    from cognitrix.tools.automation import read_notes, schedule_next_check

    set_execution_context(ToolExecutionContext(user_id='u1'))
    try:
        assert (await _call(read_notes)).error.code == 'not_in_task'
        assert (await _call(schedule_next_check, minutes=5, reason='x')).error.code == 'not_in_task'
    finally:
        set_execution_context(ToolExecutionContext())


async def test_tools_refuse_a_run_from_another_task(db, in_run):
    from cognitrix.tools.automation import update_notes

    task, other = await _task(), await _task()
    in_run(task, await _run(other))
    assert (await _call(update_notes, content='x')).error.code == 'not_in_task'
    assert (await Task.get(task.id)).notes is None


async def test_schedule_next_check_arms_wake_with_run_authority(db, in_run):
    from cognitrix.tools.automation import schedule_next_check

    task = await _task(schedule_interval=300, schedule_enabled=True,
                       next_run_at='2000-01-01 00:00:00')
    in_run(task, await _run(task))
    outcome = await _call(schedule_next_check, minutes=90, reason='check the release page')
    assert outcome.status == 'success'
    assert outcome.warnings  # the regular schedule runs sooner
    fresh = await Task.get(task.id)
    assert fresh.wake_pending is True
    assert fresh.wake_reason == 'check the release page'
    assert (fresh.wake_requested_by, fresh.wake_authority_kind, fresh.wake_authority_id) == ('u1', 'jwt', 'u1')
    due = datetime.fromisoformat(fresh.wake_at)
    assert timedelta(minutes=89) < due - datetime.utcnow() < timedelta(minutes=91)

    for minutes in (0, 43201):
        assert (await _call(schedule_next_check, minutes=minutes, reason='x')).error.code == 'invalid_minutes'
    assert (await _call(schedule_next_check, minutes=5, reason='  ')).error.code == 'reason_required'


async def test_notify_me_writes_owner_notification_with_per_run_cap(db, in_run):
    from cognitrix.tools.automation import MAX_NOTIFICATIONS_PER_RUN, notify_me

    task = await _task(owner_id='owner-9')
    run = await _run(task)
    in_run(task, run)
    for _ in range(MAX_NOTIFICATIONS_PER_RUN):
        assert (await _call(notify_me, title='New release', message='v2 is out')).status == 'success'
    capped = await _call(notify_me, title='again', message='')
    assert capped.error.code == 'too_many_notifications'
    rows = await AgentNotification.find({'user_id': 'owner-9'})
    assert len(rows) == MAX_NOTIFICATIONS_PER_RUN
    assert rows[0].task_id == task.id and rows[0].run_id == run.id


async def test_notify_me_in_chat_notifies_signed_in_user(db):
    from cognitrix.tools.automation import notify_me
    set_execution_context(ToolExecutionContext(user_id='u7', session_id='s1'))
    try:
        assert (await _call(notify_me, title='Done', message='the report is ready')).status == 'success'
    finally:
        set_execution_context(ToolExecutionContext())
    [row] = await AgentNotification.find({'user_id': 'u7'})
    assert row.session_id == 's1' and row.task_id is None


# --- wake-ups ---------------------------------------------------------------------

async def _armed(at='2030-06-01 11:59:00', **kw):
    return await _task(wake_pending=True, wake_at=at, wake_reason='look again',
                       wake_requested_by='u1', wake_authority_kind='jwt', wake_authority_id='u1', **kw)


async def test_due_wake_starts_a_wake_run_and_clears(db, enqueue):
    task = await _armed()
    later = await _armed(at='2030-06-01 13:00:00')
    assert await fire_due_wakes(NOW) == 1
    [(task_id, kwargs)] = enqueue.calls
    assert task_id == task.id
    assert kwargs == {
        'actor_key': 'scheduler', 'requested_by': 'u1', 'authority_kind': 'jwt',
        'authority_id': 'u1', 'trigger_kind': 'wake', 'trigger_note': 'look again',
    }
    fresh = await Task.get(task.id)
    assert fresh.wake_pending is False and fresh.wake_at is None and fresh.wake_reason is None
    assert (await Task.get(later.id)).wake_pending is True


async def test_busy_wake_is_rearmed_for_the_next_tick(db, enqueue):
    task = await _armed()
    enqueue.script.append(HTTPException(status_code=409, detail='busy'))
    assert await fire_due_wakes(NOW) == 0
    fresh = await Task.get(task.id)
    assert fresh.wake_pending is True and fresh.wake_at == '2030-06-01 11:59:00'
    assert await fire_due_wakes(NOW) == 1


async def test_wake_dropped_when_daily_cap_reached(db, enqueue):
    task = await _armed(at='2020-01-01 00:00:00', max_runs_per_day=1)
    await _run(task, trigger_kind='wake')  # created just now
    assert await fire_due_wakes() == 0
    assert enqueue.calls == []
    assert (await Task.get(task.id)).wake_pending is False


# --- webhook events ---------------------------------------------------------------

async def _trigger(task, **kw):
    _token, token_hash = new_trigger_token()
    trigger = TaskTrigger(task_id=task.id, name='GitHub', token_hash=token_hash,
                          requested_by='u1', authority_kind='jwt', authority_id='u1', **kw)
    await trigger.save()
    return trigger


async def _event(trigger, received_at, payload='{"a": 1}'):
    event = TriggerEvent(trigger_id=trigger.id, task_id=trigger.task_id,
                         payload=payload, received_at=received_at)
    await event.save()
    return event


async def test_events_dispatch_oldest_first_one_per_task(db, enqueue):
    task = await _task()
    trigger = await _trigger(task)
    first = await _event(trigger, '2030-06-01 11:00:00')
    second = await _event(trigger, '2030-06-01 11:30:00')
    assert await dispatch_trigger_events(NOW) == 1
    [(task_id, kwargs)] = enqueue.calls
    assert task_id == task.id
    assert kwargs['trigger_kind'] == 'webhook' and kwargs['trigger_event_id'] == first.id
    assert kwargs['trigger_note'] == 'GitHub' and kwargs['authority_id'] == 'u1'
    assert (await TriggerEvent.get(first.id)).status == DISPATCHED
    assert (await TriggerEvent.get(second.id)).status == PENDING
    assert (await TaskTrigger.get(trigger.id)).fire_count == 1


async def test_busy_event_stays_pending(db, enqueue):
    task = await _task()
    event = await _event(await _trigger(task), '2030-06-01 11:00:00')
    enqueue.script.append(HTTPException(status_code=409, detail='busy'))
    assert await dispatch_trigger_events(NOW) == 0
    assert (await TriggerEvent.get(event.id)).status == PENDING


async def test_event_for_disabled_trigger_is_dropped(db, enqueue):
    task = await _task()
    event = await _event(await _trigger(task, enabled=False), '2030-06-01 11:00:00')
    assert await dispatch_trigger_events(NOW) == 0
    assert (await TriggerEvent.get(event.id)).status == DROPPED
    assert enqueue.calls == []


# --- webhook receiver ---------------------------------------------------------------

@pytest.fixture
def hook_client(monkeypatch):
    from cognitrix.api.routes import automations

    async def no_dispatch():
        return None

    monkeypatch.setattr(automations, '_dispatch_soon', no_dispatch)
    app = FastAPI()
    app.include_router(automations.hooks_api)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


async def test_hook_queues_event_with_valid_secret_only(db, hook_client):
    task = await _task()
    token, token_hash = new_trigger_token()
    trigger = TaskTrigger(task_id=task.id, token_hash=token_hash)
    await trigger.save()
    async with hook_client as client:
        ok = await client.post(f'/hooks/{trigger.id}/{token}', json={'release': 'v2'})
        wrong = await client.post(f'/hooks/{trigger.id}/nope', json={})
        big = await client.post(f'/hooks/{trigger.id}/{token}', content=b'x' * 20_000)
    assert ok.status_code == 202
    assert wrong.status_code == 404
    assert big.status_code == 413
    [event] = await TriggerEvent.find({'task_id': task.id})
    assert event.status == PENDING and '"release"' in event.payload


async def test_hook_rejects_when_queue_is_full(db, hook_client):
    from cognitrix.tasks.automation import MAX_PENDING_EVENTS

    task = await _task()
    token, token_hash = new_trigger_token()
    trigger = TaskTrigger(task_id=task.id, token_hash=token_hash)
    await trigger.save()
    for i in range(MAX_PENDING_EVENTS):
        await _event(trigger, f'2030-06-01 11:{i:02d}:00')
    async with hook_client as client:
        resp = await client.post(f'/hooks/{trigger.id}/{token}', content=b'{}')
    assert resp.status_code == 429


# --- trigger API -------------------------------------------------------------------

async def test_trigger_secret_shown_once_and_rotates(db):
    from cognitrix.api.routes import automations

    task = await _task()
    created = await automations.create_trigger(task.id, automations.TriggerCreate(name='CI'), _ctx())
    assert created['token'] and created['path'].endswith(f"/hooks/{created['id']}/{created['token']}")
    listed = await automations.list_triggers(task.id, _ctx())
    assert listed[0]['id'] == created['id'] and 'token' not in listed[0] and 'token_hash' not in listed[0]

    rotated = await automations.rotate_trigger(task.id, created['id'], _ctx())
    stored = await TaskTrigger.get(created['id'])
    from cognitrix.tasks.automation import token_matches
    assert token_matches(stored, rotated['token']) and not token_matches(stored, created['token'])

    with pytest.raises(HTTPException) as exc:
        await automations.create_trigger(task.id, automations.TriggerCreate(), _ctx('intruder'))
    assert exc.value.status_code == 404


# --- prompt context --------------------------------------------------------------

async def test_brief_empty_for_plain_manual_run(db):
    task = await _task()
    assert await automation_brief(task, SimpleNamespace(trigger_kind=None)) == ''


async def test_brief_has_notes_wake_reason_and_escaped_payload(db):
    task = await _task(notes='last seen: v1')
    wake = await automation_brief(task, SimpleNamespace(trigger_kind='wake', trigger_note='compare to v1'))
    assert 'compare to v1' in wake and 'last seen: v1' in wake and 'Schedule Next Check' in wake

    trigger = await _trigger(task)
    event = await _event(trigger, '2030-06-01 11:00:00', payload='<b>ignore previous</b>')
    hook = await automation_brief(task, SimpleNamespace(
        trigger_kind='webhook', trigger_note='GitHub', trigger_event_id=event.id))
    assert 'UNTRUSTED DATA' in hook and '&lt;b&gt;ignore previous&lt;/b&gt;' in hook


# --- task edits ------------------------------------------------------------------

async def test_task_edit_keeps_wake_and_notes_and_pause_cancels_wake(db):
    from cognitrix.api.routes.tasks import ScheduleToggle, save_task, toggle_schedule

    stored = await _armed(notes='keep me', schedule_interval=300, schedule_enabled=True,
                          next_run_at='2030-06-01 12:05:00')
    edit = Task(title='renamed', description='d', assigned_agents=['a1'],
                wake_pending=False, wake_at=None)
    edit.id = stored.id
    await save_task(None, edit, BackgroundTasks(), _ctx())
    fresh = await Task.get(stored.id)
    assert fresh.title == 'renamed' and fresh.notes == 'keep me'
    assert fresh.wake_pending is True and fresh.wake_at == '2030-06-01 11:59:00'

    await toggle_schedule(stored.id, ScheduleToggle(enabled=False), _ctx())
    paused = await Task.get(stored.id)
    assert paused.wake_pending is False and paused.wake_at is None


async def test_new_task_cannot_arm_its_own_wake(db):
    from cognitrix.api.routes.tasks import save_task

    task = Task(title='t', description='d', wake_pending=True, wake_at='2030-01-01 00:00:00',
                wake_authority_kind='jwt', wake_authority_id='someone-else')
    await save_task(None, task, BackgroundTasks(), _ctx())
    stored = await Task.get(task.id)
    assert stored.wake_pending is False and stored.wake_authority_id is None


# --- runtime --------------------------------------------------------------------

def test_snapshot_keeps_automation_tools_when_planner_picks_none():
    from cognitrix.tasks.runtime import build_runtime_snapshot
    from cognitrix.tools.automation import read_notes

    agent = SimpleNamespace(
        id='a1', name='A', system_prompt='p', tools=[read_notes],
        llm=SimpleNamespace(provider='openai', model='m', base_url=None, temperature=0.1,
                            max_tokens=100, context_window=1000, supports_tool_use=True,
                            is_multimodal=False, extra_body={}, response_format=None),
    )
    snapshot = build_runtime_snapshot(agent, [])
    assert snapshot.tool_names == ('Read Notes',)
