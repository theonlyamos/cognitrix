import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_emitter_serializes_concurrent_sequences(monkeypatch):
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []
    active_saves = 0
    peak_active_saves = 0

    async def fake_save(self):
        nonlocal active_saves, peak_active_saves
        active_saves += 1
        peak_active_saves = max(peak_active_saves, active_saves)
        try:
            await asyncio.sleep(0)
            saved.append(self)
        finally:
            active_saves -= 1

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    emitter = TaskRunEventEmitter('run-1')

    await asyncio.gather(*[
        emitter.emit('step_status', step_index=index, data={'status': 'running'})
        for index in range(4)
    ])

    assert [event.sequence for event in saved] == [1, 2, 3, 4]
    assert peak_active_saves == 1
    assert {event.run_id for event in saved} == {'run-1'}


@pytest.mark.asyncio
async def test_text_is_immediate_then_batched_and_flushed(monkeypatch):
    import cognitrix.tasks.events as events
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []
    now = [10.0]

    async def fake_save(self):
        saved.append(self)

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    monkeypatch.setattr(events.time, 'monotonic', lambda: now[0])
    emitter = TaskRunEventEmitter('run-1')
    common = {
        'session_id': 'session-1',
        'step_index': 0,
        'agent_name': 'Researcher',
        'turn_id': 'session-1:1',
        'attempt': 1,
    }

    await emitter.text_delta(content='first', **common)
    await emitter.text_delta(content=' second', **common)
    assert [event.data['content'] for event in saved] == ['first']

    now[0] += 0.2
    await emitter.text_delta(content=' third', **common)
    await emitter.text_delta(content=' remainder', **common)
    await emitter.flush_text(session_id='session-1', turn_id='session-1:1')

    assert [event.data['content'] for event in saved] == [
        'first',
        ' second third',
        ' remainder',
    ]


@pytest.mark.asyncio
async def test_text_flushes_at_character_threshold(monkeypatch):
    import cognitrix.tasks.events as events
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []

    async def fake_save(self):
        saved.append(self)

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    monkeypatch.setattr(events.time, 'monotonic', lambda: 10.0)
    emitter = TaskRunEventEmitter('run-1')
    common = {
        'session_id': 'session-1',
        'step_index': 0,
        'agent_name': 'Researcher',
        'turn_id': 'session-1:1',
        'attempt': 1,
    }

    await emitter.text_delta(content='first', **common)
    await emitter.text_delta(content='x' * 255, **common)
    assert [event.data['content'] for event in saved] == ['first']

    await emitter.text_delta(content='y', **common)

    assert [event.data['content'] for event in saved] == ['first', 'x' * 255 + 'y']


@pytest.mark.asyncio
async def test_text_buffers_are_independent_per_turn(monkeypatch):
    import cognitrix.tasks.events as events
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []

    async def fake_save(self):
        saved.append(self)

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    monkeypatch.setattr(events.time, 'monotonic', lambda: 10.0)
    emitter = TaskRunEventEmitter('run-1')
    common = {
        'session_id': 'session-1',
        'step_index': 0,
        'agent_name': 'Researcher',
        'attempt': 1,
    }

    await emitter.text_delta(turn_id='turn-1', content='one', **common)
    await emitter.text_delta(turn_id='turn-2', content='two', **common)
    await emitter.text_delta(turn_id='turn-1', content=' pending', **common)
    await emitter.text_delta(turn_id='turn-2', content=' waiting', **common)
    await emitter.flush_text(session_id='session-1', turn_id='turn-1')

    assert [(event.data['turn_id'], event.data['content']) for event in saved] == [
        ('turn-1', 'one'),
        ('turn-2', 'two'),
        ('turn-1', ' pending'),
    ]

    await emitter.flush_text(session_id='session-1', turn_id='turn-2')

    assert (saved[-1].data['turn_id'], saved[-1].data['content']) == ('turn-2', ' waiting')


@pytest.mark.asyncio
async def test_event_write_failure_is_non_fatal(monkeypatch):
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    async def fail_save(self):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(TaskRunEvent, 'save', fail_save)
    emitter = TaskRunEventEmitter('run-1')

    assert await emitter.emit('run_status', data={'status': 'running'}) is None


@pytest.mark.asyncio
async def test_durable_emitter_propagates_lease_loss(monkeypatch):
    """A fenced worker must stop instead of silently continuing execution."""
    from cognitrix.tasks.events import TaskRunEventEmitter
    from cognitrix.tasks.repository import LeaseClaim, LeaseLost, RunRepository

    claim = LeaseClaim(run_id='run-1', owner='worker-old', generation=1)

    async def lose_lease(self, *args, **kwargs):
        raise LeaseLost('worker-old was fenced')

    monkeypatch.setattr(RunRepository, 'emit_event', lose_lease)
    emitter = TaskRunEventEmitter('run-1', claim=claim)

    with pytest.raises(LeaseLost, match='fenced'):
        await emitter.emit('step_status', data={'status': 'running'})


@pytest.mark.asyncio
async def test_task_run_event_sqlite_round_trip(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.events import TaskRunEvent, event_payload, events_after

    db_file = str(tmp_path / 'events.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    create = getattr(TaskRunEvent, '_create_table_async', None) or TaskRunEvent.create_table
    await create()

    event = TaskRunEvent(
        run_id='run-1',
        session_id='session-1',
        step_index=0,
        sequence=1,
        kind='tool_started',
        agent_name='Researcher',
        data={'tool_call_id': 'call-1', 'params': '{"q":"x"}'},
    )
    await event.save()

    loaded = await TaskRunEvent.find_one({'run_id': 'run-1'})
    assert loaded is not None
    assert loaded.data == {'tool_call_id': 'call-1', 'params': '{"q":"x"}'}
    assert event_payload(loaded)['type'] == 'task_run_event'
    assert event_payload(loaded)['sequence'] == 1

    for run_id, sequence in (('run-1', 3), ('run-2', 99), ('run-1', 2)):
        await TaskRunEvent(
            run_id=run_id,
            sequence=sequence,
            kind='step_status',
            data={'status': 'running'},
        ).save()

    replay = await events_after('run-1', 1)

    assert [(row.run_id, row.sequence) for row in replay] == [
        ('run-1', 2),
        ('run-1', 3),
    ]

    bounded = await events_after('run-1', 0, limit=2)
    assert [row.sequence for row in bounded] == [1, 2]


@pytest.mark.parametrize('dbms', ['sqlite', 'postgresql', 'mysql'])
@pytest.mark.asyncio
async def test_events_after_uses_indexed_ordered_bounded_relational_query(
    monkeypatch,
    dbms,
):
    from odbms import DBMS

    from cognitrix.tasks.events import events_after

    calls = []

    class Cursor:
        description = [('id',), ('run_id',), ('sequence',), ('kind',), ('data',)]

        def fetchall(self):
            return [('event-3', 'run-1', 3, 'status', '{}')]

    class Database:
        async def query(self, statement, params=None):
            calls.append((statement, params))
            return Cursor()

    database = Database()
    database.dbms = dbms
    monkeypatch.setattr(DBMS, 'Database', database)

    rows = await events_after('run-1', 2, limit=7)

    assert [row.sequence for row in rows] == [3]
    statement, params = calls[-1]
    lowered = statement.lower()
    assert 'where run_id =' in lowered
    assert 'sequence >' in lowered
    assert 'order by sequence asc' in lowered
    assert 'limit' in lowered
    assert params['run_id'] == 'run-1'
    assert params['sequence'] == 2
    assert params['limit'] == 7


@pytest.mark.asyncio
async def test_events_after_uses_native_mongodb_range_sort_and_limit(monkeypatch):
    from odbms import DBMS

    from cognitrix.tasks.events import events_after

    calls = []

    class Database:
        dbms = 'mongodb'

        async def find(self, table, conditions, **options):
            calls.append((table, conditions, options))
            return [{
                'id': 'event-4',
                'run_id': 'run-1',
                'sequence': 4,
                'kind': 'status',
                'data': {},
            }]

    monkeypatch.setattr(DBMS, 'Database', Database())

    rows = await events_after('run-1', 3, limit=9)

    assert [row.sequence for row in rows] == [4]
    assert calls == [(
        'taskrunevents',
        {'run_id': 'run-1', 'sequence': {'$gt': 3}},
        {'limit': 9, 'sort': [('sequence', 1)]},
    )]


def test_task_run_event_is_registered_in_api_and_cli_startup():
    root = Path(__file__).resolve().parents[1]
    config_source = (root / 'cognitrix' / 'config.py').read_text()
    cli_source = (root / 'cognitrix' / 'cli' / 'core.py').read_text()

    assert 'from cognitrix.tasks.events import TaskRunEvent' in config_source
    assert 'TaskRunEvent' in config_source.split('for model in (', 1)[1]
    assert 'from cognitrix.tasks.events import TaskRunEvent' in cli_source
    assert 'TaskRunEvent' in cli_source.split('for model in (', 1)[1]


def _tool_event(sequence, kind, data, *, step_index=0):
    from cognitrix.tasks.events import TaskRunEvent

    return TaskRunEvent(
        run_id='run-1',
        session_id='session-1',
        step_index=step_index,
        sequence=sequence,
        kind=kind,
        agent_name='Researcher',
        data=data,
    )


def test_project_step_tool_calls_pairs_ids_and_keeps_event_order():
    from cognitrix.tasks.events import project_step_tool_calls

    calls = project_step_tool_calls([
        _tool_event(1, 'tool_started', {
            'tool_call_id': 'call-1',
            'tool_name': 'Search',
            'params': {'query': 'OpenAI'},
        }),
        _tool_event(2, 'text_delta', {'content': 'ignored'}),
        _tool_event(3, 'tool_completed', {
            'tool_call_id': 'call-1',
            'tool_name': 'Search',
            'status': 'done',
            'result': 'https://openai.com/news/',
        }),
        _tool_event(4, 'tool_completed', {
            'tool_call_id': 'call-missed',
            'tool_name': 'Read',
            'status': 'error',
            'result': 'missing file',
        }),
        _tool_event(5, 'tool_started', {
            'tool_call_id': 'other-step',
            'tool_name': 'Write',
            'params': '{}',
        }, step_index=1),
    ], 0)

    assert calls == [{
        'id': 'call-1',
        'name': 'Search',
        'args': '{"query": "OpenAI"}',
        'status': 'done',
        'result': 'https://openai.com/news/',
    }, {
        'id': 'call-missed',
        'name': 'Read',
        'args': '',
        'status': 'error',
        'result': 'missing file',
    }]


def test_project_step_tool_calls_pairs_idless_same_name_calls_fifo():
    from cognitrix.tasks.events import project_step_tool_calls

    calls = project_step_tool_calls([
        _tool_event(1, 'tool_started', {'tool_name': 'Read', 'params': 'first'}),
        _tool_event(2, 'tool_started', {'tool_name': 'Read', 'params': 'second'}),
        _tool_event(3, 'tool_completed', {
            'tool_name': 'Read', 'status': 'done', 'result': 'one',
        }),
        _tool_event(4, 'tool_completed', {
            'tool_name': 'Read', 'status': 'error', 'result': 'two',
        }),
    ], 0)

    assert [(call['args'], call['status'], call['result']) for call in calls] == [
        ('first', 'done', 'one'),
        ('second', 'error', 'two'),
    ]


def test_project_step_tool_calls_does_not_pair_unmatched_explicit_id_by_name():
    from cognitrix.tasks.events import project_step_tool_calls

    calls = project_step_tool_calls([
        _tool_event(1, 'tool_started', {
            'tool_call_id': 'call-A',
            'tool_name': 'Search',
            'params': 'first',
        }),
        _tool_event(2, 'tool_completed', {
            'tool_call_id': 'call-B',
            'tool_name': 'Search',
            'status': 'done',
            'result': 'second result',
        }),
        _tool_event(3, 'tool_completed', {
            'tool_call_id': 'call-A',
            'tool_name': 'Search',
            'status': 'done',
            'result': 'first result',
        }),
    ], 0)

    assert calls == [{
        'id': 'call-A',
        'name': 'Search',
        'args': 'first',
        'status': 'done',
        'result': 'first result',
    }, {
        'id': 'call-B',
        'name': 'Search',
        'args': '',
        'status': 'done',
        'result': 'second result',
    }]


@pytest.mark.asyncio
async def test_step_tool_calls_pages_until_short_page(monkeypatch):
    import cognitrix.tasks.events as events
    from cognitrix.tasks.events import step_tool_calls

    cursors = []
    first_page = [
        _tool_event(1, 'text_delta', {'content': 'ignored'}),
        _tool_event(2, 'tool_started', {
            'tool_call_id': 'call-1',
            'tool_name': 'Search',
            'params': {'query': 'OpenAI'},
        }),
    ]
    second_page = [_tool_event(3, 'tool_completed', {
        'tool_call_id': 'call-1',
        'tool_name': 'Search',
        'status': 'done',
        'result': 'https://openai.com/news/',
    })]

    async def fake_events_after(run_id, sequence, *, limit=256):
        assert run_id == 'run-1'
        cursors.append(sequence)
        return {0: first_page, 2: second_page}[sequence]

    monkeypatch.setattr(events, 'EVENT_PAGE_SIZE', 2)
    monkeypatch.setattr(events, 'events_after', fake_events_after)

    assert await step_tool_calls('run-1', 0) == [{
        'id': 'call-1',
        'name': 'Search',
        'args': '{"query": "OpenAI"}',
        'status': 'done',
        'result': 'https://openai.com/news/',
    }]
    assert cursors == [0, 2]
