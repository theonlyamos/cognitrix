"""Results of agent-created tasks going back to the agent that created them (tasks/reports.py)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.tasks import reports
from cognitrix.tasks.automation import AgentNotification, TaskTrigger, TriggerEvent, automation_brief
from cognitrix.tasks.base import Task
from cognitrix.tasks.reports import TaskReport, TaskReportClaim
from cognitrix.tasks.run import TaskRun, TaskRunStatus
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

NOW = datetime(2030, 6, 1, 12, 0, 0)


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.run import TaskRunHead

    db_file = str(tmp_path / 'reports.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (Task, TaskRun, TaskRunHead, TaskTrigger, TriggerEvent, AgentNotification, TaskReport,
                  TaskReportClaim):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()
    await reports.ensure_schema()
    reports._last_reconcile.clear()


@pytest.fixture(autouse=True)
def no_push(monkeypatch):
    from cognitrix.common import webpush

    sent = []
    monkeypatch.setattr(webpush, 'send_soon', lambda *a, **k: sent.append((a, k)))
    return sent


class Agent:
    def __init__(self, id='agent-1', name='Scout'):
        self.id, self.name = id, name


def chat_creator(**kw):
    data = {'agent_id': 'agent-1', 'agent_name': 'Scout', 'user_id': 'u1', 'requested_by': 'u1',
            'authority_kind': 'jwt', 'authority_id': 'u1', 'kind': 'chat', 'session_id': 'chat-1',
            'depth': 1}
    data.update(kw)
    return data


async def make_task(**kw) -> Task:
    data = {'title': 'Find flights', 'description': 'Look for flights', 'owner_id': 'u1',
            'assigned_agents': ['agent-2'], 'created_by': chat_creator()}
    data.update(kw)
    task = Task(**data)
    await task.save()
    return task


async def make_run(task, status=TaskRunStatus.COMPLETED, result='Cheapest is 420 USD', error=None,
                   completed_at=None) -> TaskRun:
    run = TaskRun(task_id=task.id, status=status, result=result, error=error, acl_version=1,
                  completed_at=completed_at)
    await run.save()
    return run


# --- recording the creator --------------------------------------------------------

@pytest.mark.asyncio
async def test_creator_snapshot_in_a_chat_and_in_a_task_run(db):
    chat = await reports.creator_snapshot(Agent(), ToolExecutionContext(user_id='u1', session_id='chat-1'))
    assert chat['kind'] == 'chat' and chat['session_id'] == 'chat-1' and chat['depth'] == 1
    assert chat['agent_id'] == 'agent-1' and chat['agent_name'] == 'Scout'

    origin = await make_task(created_by=chat_creator(depth=2))
    nested = await reports.creator_snapshot(
        Agent(), ToolExecutionContext(user_id='u1', session_id='s', task_id=origin.id, run_id='r1'))
    assert nested['kind'] == 'task' and nested['task_id'] == origin.id and nested['depth'] == 3

    deepest = await make_task(created_by=chat_creator(depth=3))
    with pytest.raises(ValueError, match='nest 3 levels'):
        await reports.creator_snapshot(Agent(), ToolExecutionContext(user_id='u1', task_id=deepest.id))

    assert await reports.creator_snapshot(Agent(), ToolExecutionContext(session_id='chat-1')) is None
    assert await reports.creator_snapshot(None, ToolExecutionContext(user_id='u1')) is None


def test_defaults_and_public_projection():
    one_off = SimpleNamespace(created_by=chat_creator(), report_to=None)
    scheduled = SimpleNamespace(created_by=chat_creator(), report_to=None, schedule_cron='0 9 * * *')
    mine = SimpleNamespace(created_by=None, report_to='creator')
    assert reports.effective_report_to(one_off) == 'creator'
    assert reports.effective_report_to(scheduled) == 'person'
    assert reports.effective_report_to(mine) == 'person'
    assert reports.effective_report_to(SimpleNamespace(created_by=chat_creator(), report_to='both')) == 'both'
    public = reports.public_creator(chat_creator())
    assert 'authority_id' not in public and 'requested_by' not in public and public['agent_id'] == 'agent-1'
    assert reports.validate_report_fields('nobody', None)
    assert reports.validate_report_fields('both', 'changed') is None


@pytest.mark.asyncio
async def test_create_task_records_the_calling_agent(db, monkeypatch):
    from cognitrix.tools.management import create_task

    token = set_execution_context(ToolExecutionContext(user_id='u1', session_id='chat-1', agent_id='agent-1'))
    try:
        out = await create_task.run('Find flights', 'Look', parent=Agent())
        bad = await create_task.run('X', 'Y', report_to='everyone', parent=Agent())
    finally:
        reset_execution_context(token)
    assert out.outcome.status == 'success'
    assert 'comes back to you into this chat' in ' '.join(out.outcome.warnings)
    task = (await Task.find({'title': 'Find flights'}))[0]
    assert task.created_by['kind'] == 'chat' and task.created_by['agent_id'] == 'agent-1'
    assert reports.effective_report_to(task) == 'creator'
    assert bad.outcome.error.code == 'invalid_task'

    # Without a chat or task run there is nowhere to send a result back.
    token = set_execution_context(ToolExecutionContext(user_id='u1'))
    try:
        refused = await create_task.run('Z', 'Z', report_to='creator', parent=Agent())
    finally:
        reset_execution_context(token)
    assert refused.outcome.error.code == 'cannot_report_back'


# --- queueing ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_report_once_per_run_and_batches_runs(db):
    task = await make_task()
    first = await make_run(task)
    assert await reports.queue_report(task, first) is True  # the person's push is skipped
    assert await reports.queue_report(task, first) is True  # idempotent
    second = await make_run(task, TaskRunStatus.FAILED, result=None, error='site down')
    await reports.queue_report(task, second)
    rows = await TaskReport.find({'task_id': task.id})
    assert len(rows) == 1
    assert rows[0].run_ids == [first.id, second.id] and rows[0].run_statuses == ['completed', 'failed']
    assert rows[0].target_kind == 'chat' and rows[0].target_id == 'chat-1'

    text = await reports.report_text(rows[0], task, for_chat=True)
    assert 'ran 2 times' in text and 'site down' in text and 'Notify Me' in text


@pytest.mark.asyncio
async def test_person_tasks_cancelled_runs_and_report_only_on(db):
    person = await make_task(report_to='person')
    assert await reports.queue_report(person, await make_run(person)) is False
    both = await make_task(report_to='both')
    assert await reports.queue_report(both, await make_run(both)) is False  # person still pushed
    assert len(await TaskReport.find({'task_id': both.id})) == 1

    task = await make_task()
    await reports.queue_report(task, await make_run(task, TaskRunStatus.CANCELLED))
    assert not await TaskReport.find({'task_id': task.id})

    failures = await make_task(report_only_on='failure')
    await reports.queue_report(failures, await make_run(failures))
    assert not await TaskReport.find({'task_id': failures.id})

    changed = await make_task(report_only_on='changed')
    await reports.queue_report(changed, await make_run(changed, result='Price 420'))
    await TaskReport.update_one({'task_id': changed.id}, {'status': reports.DELIVERED})
    await reports.queue_report(changed, await make_run(changed, result='Price  420'))
    assert len(await TaskReport.find({'task_id': changed.id})) == 1
    await reports.queue_report(changed, await make_run(changed, result='Price 390'))
    assert len(await TaskReport.find({'task_id': changed.id})) == 2


@pytest.mark.asyncio
async def test_repeated_failures_also_tell_the_person(db):
    task = await make_task()
    for _ in range(reports.MAX_FAILED_REPORTS):
        await reports.queue_report(task, await make_run(task, TaskRunStatus.FAILED, result=None, error='boom'))
        await TaskReport.update_one({'task_id': task.id, 'status': reports.PENDING}, {'status': reports.DELIVERED})
    assert not await AgentNotification.find({'user_id': 'u1'})
    await reports.queue_report(task, await make_run(task, TaskRunStatus.FAILED, result=None, error='boom'))
    notes = await AgentNotification.find({'user_id': 'u1'})
    assert len(notes) == 1 and 'failed' in notes[0].title


# --- delivery into the chat ------------------------------------------------------------

@pytest.fixture
def chat(monkeypatch):
    import cognitrix.agents.access as access
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    state = {'outcome': 'started', 'calls': [], 'session': SimpleNamespace(id='chat-1', user_id='u1', agent_id='agent-1')}

    async def find_visible_agent(user_key, agent_id=None, name=None):
        return Agent() if agent_id == 'agent-1' else None

    async def get(sid):
        return state['session'] if sid == 'chat-1' else None

    async def deliver_report(user_key, agent_id, session_id, text, **kw):
        state['calls'].append((user_key, agent_id, session_id, text, kw))
        return state['outcome']

    monkeypatch.setattr(access, 'find_visible_agent', find_visible_agent)
    monkeypatch.setattr(Session, 'get', get)
    monkeypatch.setattr(agent_chats, 'deliver_report', deliver_report)
    return state


@pytest.mark.asyncio
async def test_report_goes_into_the_chat_quietly_with_notify_me(db, chat):
    task = await make_task()
    await reports.queue_report(task, await make_run(task))
    assert await reports.deliver_due_reports(NOW + timedelta(days=3650)) == 1
    row = (await TaskReport.find({'task_id': task.id}))[0]
    assert row.status == reports.DELIVERED and row.outcome == 'started'
    user, agent_id, session_id, text, kw = chat['calls'][0]
    assert (user, agent_id, session_id) == ('u1', 'agent-1', 'chat-1')
    from cognitrix.sessions.agent_chats import REPORT_PREFIX
    assert text.startswith(REPORT_PREFIX) and 'Cheapest is 420 USD' in text and 'UNTRUSTED' in text
    assert kw['wait_seconds'] == 0 and kw['on_done'] is not None
    assert [t.name for t in kw['extra_tools']] == ['Notify Me']


@pytest.mark.asyncio
async def test_busy_chat_retries_and_missing_chat_falls_back_to_the_person(db, chat):
    task = await make_task()
    await reports.queue_report(task, await make_run(task))
    chat['outcome'] = 'dropped'
    now = reports._now()
    await reports.deliver_due_reports(now)
    row = (await TaskReport.find({'task_id': task.id}))[0]
    assert row.status == reports.PENDING and row.next_at > now.strftime(reports.FMT)
    assert await reports.deliver_due_reports(now) == 0  # not due yet

    chat['session'] = None
    await reports.deliver_due_reports(now + timedelta(minutes=5))
    row = (await TaskReport.find({'task_id': task.id}))[0]
    assert row.status == reports.FALLBACK and 'chat' in row.outcome
    notes = await AgentNotification.find({'user_id': 'u1'})
    assert len(notes) == 1 and notes[0].link == f'/tasks/{task.id}'


# --- delivery to a task run ---------------------------------------------------------------

@pytest.fixture
def enqueue(monkeypatch):
    import cognitrix.api.routes.tasks as task_routes

    calls, script = [], []

    async def fake(task, resume=False, **kw):
        calls.append((task.id, kw))
        if script:
            raise script.pop(0)
        return SimpleNamespace(id='creator-run-1')

    monkeypatch.setattr(task_routes, '_enqueue_task_start', fake)
    return SimpleNamespace(calls=calls, script=script)


@pytest.mark.asyncio
async def test_report_starts_a_run_of_the_creating_task(db, enqueue):
    parent = await make_task(title='Plan trip', assigned_agents=['agent-1'], created_by=None)
    child = await make_task(created_by=chat_creator(kind='task', task_id=parent.id, session_id=None, depth=1))
    await reports.queue_report(child, await make_run(child))

    enqueue.script.append(HTTPException(status_code=409))
    now = reports._now()
    await reports.deliver_due_reports(now)
    row = (await TaskReport.find({'task_id': child.id}))[0]
    assert row.status == reports.PENDING  # the creating run is still going

    await reports.deliver_due_reports(now + timedelta(minutes=1))
    row = (await TaskReport.find({'task_id': child.id}))[0]
    assert row.status == reports.DELIVERED and row.delivered_run_id == 'creator-run-1'
    task_id, kw = enqueue.calls[-1]
    assert task_id == parent.id and kw['trigger_kind'] == 'task_result' and kw['trigger_event_id'] == row.id
    assert kw['requested_by'] == 'u1' and kw['authority_kind'] == 'jwt'

    run = SimpleNamespace(trigger_kind='task_result', trigger_note='Result of "Find flights"',
                          trigger_event_id=row.id)
    brief = await automation_brief(parent, run)
    assert 'a task that this task created has ended' in brief and 'Cheapest is 420 USD' in brief
    assert 'Notify Me' in brief


@pytest.mark.asyncio
async def test_creating_task_gone_falls_back(db, enqueue):
    child = await make_task(created_by=chat_creator(kind='task', task_id='missing', session_id=None))
    await reports.queue_report(child, await make_run(child))
    await reports.deliver_due_reports(reports._now())
    row = (await TaskReport.find({'task_id': child.id}))[0]
    assert row.status == reports.FALLBACK and not enqueue.calls
    assert await AgentNotification.find({'user_id': 'u1'})


@pytest.mark.asyncio
async def test_stale_claims_are_recovered(db, chat):
    task = await make_task()
    await reports.queue_report(task, await make_run(task))
    old = (reports._now() - timedelta(hours=1)).strftime(reports.FMT)
    await TaskReport.update_one({'task_id': task.id}, {'status': reports.DELIVERING, 'claimed_at': old})
    await reports.deliver_due_reports(reports._now())
    assert (await TaskReport.find({'task_id': task.id}))[0].status == reports.DELIVERED


# --- completion hook ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_queues_the_report_for_every_run(db, no_push):
    from cognitrix.tasks.completion import deliver_completion_notification

    task = await make_task()
    run = await make_run(task)
    await deliver_completion_notification(run.id)
    assert len(await TaskReport.find({'task_id': task.id})) == 1
    assert not no_push


# --- deliver_report options ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deliver_report_adds_tools_and_custom_follow_up(monkeypatch):
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    class Model(SimpleNamespace):
        def model_copy(self, update):
            return Model(**{**self.__dict__, **update})

    agent = Model(id='agent-1', name='Scout', tools=[SimpleNamespace(name='Search')])
    session = SimpleNamespace(id='chat-1', agent_id='agent-1', user_id='u1', chat=[])
    started = []

    async def find_visible_agent(user_key, agent_id=None, name=None):
        return agent

    async def get(sid):
        return session

    monkeypatch.setattr('cognitrix.agents.access.find_visible_agent', find_visible_agent)
    monkeypatch.setattr(Session, 'get', get)
    monkeypatch.setattr(agent_chats, 'run_in_background',
                        lambda user, a, s, text, on_done=None, **kw: started.append((a, on_done)))

    async def done(status, reply):
        return None

    notify = SimpleNamespace(name='Notify Me')
    out = await agent_chats.deliver_report('u1', 'agent-1', 'chat-1', agent_chats.REPORT_PREFIX + 'x',
                                           sender='T', wait_seconds=0, on_done=done, extra_tools=[notify])
    assert out == 'started'
    used, follow_up = started[0]
    assert [t.name for t in used.tools] == ['Search', 'Notify Me'] and follow_up is done
    assert [t.name for t in agent.tools] == ['Search']  # the stored agent is not changed


# --- review fixes: atomic claims, reload after claim, missed runs ---------------------------------

@pytest.mark.asyncio
async def test_one_claim_per_run_even_when_racing(db):
    import asyncio

    task = await make_task()
    run = await make_run(task)
    await asyncio.gather(*(reports.queue_report(task, run) for _ in range(4)))
    rows = await TaskReport.find({'task_id': task.id})
    assert len(rows) == 1 and rows[0].run_ids == [run.id]
    assert len(await TaskReportClaim.find({'run_id': run.id})) == 1
    # The unique index refuses a second claim row outright.
    with pytest.raises(Exception, match='(?i)unique'):
        await TaskReportClaim(run_id=run.id, task_id=task.id).save()


@pytest.mark.asyncio
async def test_delivery_uses_runs_added_after_the_scan(db, chat, monkeypatch):
    task = await make_task()
    await reports.queue_report(task, await make_run(task, result='first'))
    late = await make_run(task, result='second result')
    original = TaskReport.find

    async def scan_then_append(query=None, *a, **kw):
        rows = await original(query, *a, **kw)
        if query == {'status': reports.PENDING} and not getattr(scan_then_append, 'done', False):
            scan_then_append.done = True
            await reports.queue_report(task, late)  # lands between the scan and the claim
        return rows

    monkeypatch.setattr(TaskReport, 'find', scan_then_append)
    await reports.deliver_due_reports(reports._now())
    text = chat['calls'][0][3]
    assert 'ran 2 times' in text and 'second result' in text


@pytest.mark.asyncio
async def test_reconcile_queues_runs_that_never_reached_completion(db):
    task = await make_task()
    now = reports._now()
    ended = (now - timedelta(minutes=10)).strftime(reports.FMT)
    missed = await make_run(task, completed_at=ended)
    fresh = await make_run(task, completed_at=now.strftime(reports.FMT))  # completion may still be on it
    old = await make_run(task, completed_at=(now - timedelta(days=3)).strftime(reports.FMT))
    assert await reports.reconcile_missing_reports(now, force=True) == 1
    rows = await TaskReport.find({'task_id': task.id})
    assert [r.run_ids for r in rows] == [[missed.id]]
    assert not await TaskReportClaim.find({'run_id': fresh.id})
    assert not await TaskReportClaim.find({'run_id': old.id})
    # A second pass finds nothing new, and the sweep is rate-limited.
    assert await reports.reconcile_missing_reports(now, force=True) == 0
    assert await reports.reconcile_missing_reports(now + timedelta(minutes=1)) == 0
