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


def test_task_run_event_is_registered_in_api_and_cli_startup():
    root = Path(__file__).resolve().parents[1]
    config_source = (root / 'cognitrix' / 'config.py').read_text()
    cli_source = (root / 'cognitrix' / 'cli' / 'core.py').read_text()

    assert 'from cognitrix.tasks.events import TaskRunEvent' in config_source
    assert 'TaskRunEvent' in config_source.split('for model in (', 1)[1]
    assert 'from cognitrix.tasks.events import TaskRunEvent' in cli_source
    assert 'TaskRunEvent' in cli_source.split('for model in (', 1)[1]
