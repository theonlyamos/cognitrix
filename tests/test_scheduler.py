"""Task schedule fields, next-run math, and scheduler tick behavior."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from cognitrix.tasks.base import Task
from cognitrix.tasks.scheduler import compute_next_run, tick, validate_schedule

NOW = datetime(2030, 6, 1, 12, 0, 0)


# --- model fields ------------------------------------------------------------

def test_schedule_fields_default_off():
    t = Task(title='t', description='d')
    assert t.schedule_at is None and t.schedule_interval is None and t.schedule_cron is None
    assert t.next_run_at is None and t.schedule_enabled is False


def test_schedule_enabled_null_coerces():
    """Pre-migration rows read back NULL for the new bool column."""
    t = Task(title='t', description='d', schedule_enabled=None)
    assert t.schedule_enabled is False


async def test_task_schedule_sqlite_roundtrip(tmp_path):
    """Schedule fields + CAS claim through the actual sqlite adapter."""
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / 'sched-test.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()

    create = getattr(Task, '_create_table_async', None) or Task.create_table
    await create()

    t = Task(title='sched', description='d', schedule_interval=300,
             next_run_at='2030-06-01 12:00:00', schedule_enabled=True)
    await t.save()

    found = await Task.find({'schedule_enabled': True})
    assert [x.id for x in found] == [t.id]
    fetched = found[0]
    assert fetched.schedule_interval == 300
    assert fetched.next_run_at == '2030-06-01 12:00:00'
    assert fetched.schedule_at is None and fetched.schedule_cron is None

    # CAS claim: matching next_run_at wins exactly once.
    claimed = await Task.update_one(
        {'id': t.id, 'next_run_at': '2030-06-01 12:00:00'},
        {'next_run_at': '2030-06-01 12:05:00'})
    assert claimed == 1
    lost = await Task.update_one(
        {'id': t.id, 'next_run_at': '2030-06-01 12:00:00'},
        {'next_run_at': '2030-06-01 12:10:00'})
    assert lost == 0

    # Disabling a one-shot sets NULLs through update_one.
    await Task.update_one({'id': t.id}, {'next_run_at': None, 'schedule_enabled': False})
    again = await Task.get(t.id)
    assert again.next_run_at is None and again.schedule_enabled is False


# --- validation ---------------------------------------------------------------

def _task(**kw):
    return Task(title='t', description='d', **kw)


def test_validate_rejects_multiple_types():
    t = _task(schedule_at='2030-06-02 09:00:00', schedule_interval=300)
    assert 'at most one' in validate_schedule(t, respecified=True)


def test_validate_interval_floor():
    assert 'at least 60' in validate_schedule(_task(schedule_interval=5), respecified=True)
    assert validate_schedule(_task(schedule_interval=60), respecified=True) is None


def test_validate_bad_cron():
    assert 'invalid cron' in validate_schedule(_task(schedule_cron='not a cron'), respecified=True)
    assert validate_schedule(_task(schedule_cron='*/5 * * * *'), respecified=True) is None


def test_validate_past_oneshot_only_when_respecified():
    t = _task(schedule_at='2020-01-01 00:00:00')
    assert 'in the past' in validate_schedule(t, respecified=True, now=NOW)
    # Carried over on an unrelated edit: must not block.
    assert validate_schedule(t, respecified=False, now=NOW) is None


def test_validate_garbage_datetime():
    assert 'valid datetime' in validate_schedule(_task(schedule_at='garbage'), respecified=True)


# --- next-run math ------------------------------------------------------------

def test_next_run_oneshot_passthrough():
    t = _task(schedule_at='2030-06-02 09:00:00')
    assert compute_next_run(t, NOW) == '2030-06-02 09:00:00'


def test_next_run_interval():
    t = _task(schedule_interval=300)
    assert compute_next_run(t, NOW) == '2030-06-01 12:05:00'


def test_next_run_cron_is_utc():
    """Cron runs on the server clock; the stored instant must be UTC."""
    t = _task(schedule_cron='*/15 * * * *')
    got = datetime.fromisoformat(compute_next_run(t, NOW))
    # Independently derive the expected instant with the same contract.
    local_tz = datetime.now().astimezone().tzinfo
    local_now = NOW.replace(tzinfo=timezone.utc).astimezone(local_tz)
    from croniter import croniter
    expected = croniter('*/15 * * * *', local_now).get_next(datetime)
    assert got == expected.astimezone(timezone.utc).replace(tzinfo=None)
    assert timedelta(0) < got - NOW <= timedelta(minutes=15)


def test_next_run_none_without_schedule():
    assert compute_next_run(_task(), NOW) is None


# --- tick ---------------------------------------------------------------------

@pytest.fixture
async def sched_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / 'tick-test.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    create = getattr(Task, '_create_table_async', None) or Task.create_table
    await create()


@pytest.fixture
def enqueue(monkeypatch):
    """Mock _enqueue_task_start at its import source; scripted exceptions."""
    import cognitrix.api.routes.tasks as task_routes

    calls: list = []
    script: list = []

    async def fake_enqueue(task, resume=False):
        calls.append(task)
        if script:
            raise script.pop(0)
        return task

    monkeypatch.setattr(task_routes, '_enqueue_task_start', fake_enqueue)
    fake_enqueue.calls = calls
    fake_enqueue.script = script
    return fake_enqueue


async def _mk(**kw):
    t = Task(title='t', description='d', schedule_enabled=True, **kw)
    await t.save()
    return t


async def test_tick_fires_due_interval_and_advances(sched_db, enqueue):
    t = await _mk(schedule_interval=300, next_run_at='2030-06-01 11:59:00')
    assert await tick(NOW) == 1
    assert len(enqueue.calls) == 1
    fresh = await Task.get(t.id)
    assert fresh.next_run_at == '2030-06-01 12:05:00'
    assert fresh.schedule_enabled is True


async def test_tick_skips_not_due(sched_db, enqueue):
    await _mk(schedule_interval=300, next_run_at='2030-06-01 12:01:00')
    assert await tick(NOW) == 0
    assert enqueue.calls == []


async def test_tick_oneshot_fires_once_and_disables(sched_db, enqueue):
    t = await _mk(schedule_at='2030-06-01 11:00:00', next_run_at='2030-06-01 11:00:00')
    assert await tick(NOW) == 1
    fresh = await Task.get(t.id)
    assert fresh.schedule_enabled is False and fresh.next_run_at is None
    # Next tick: nothing left to fire.
    assert await tick(NOW) == 0


async def test_tick_409_recurring_skips_occurrence(sched_db, enqueue):
    t = await _mk(schedule_interval=300, next_run_at='2030-06-01 11:59:00')
    enqueue.script.append(HTTPException(status_code=409, detail='active'))
    assert await tick(NOW) == 0
    fresh = await Task.get(t.id)
    # Occurrence dropped but schedule advanced — no revert.
    assert fresh.next_run_at == '2030-06-01 12:05:00'


async def test_tick_409_oneshot_reverts_claim(sched_db, enqueue):
    t = await _mk(schedule_at='2030-06-01 11:00:00', next_run_at='2030-06-01 11:00:00')
    enqueue.script.append(HTTPException(status_code=409, detail='active'))
    assert await tick(NOW) == 0
    fresh = await Task.get(t.id)
    # Claim restored: retries next tick, fires when the active run ends.
    assert fresh.schedule_enabled is True
    assert fresh.next_run_at == '2030-06-01 11:00:00'


async def test_tick_503_reverts_claim(sched_db, enqueue):
    t = await _mk(schedule_interval=300, next_run_at='2030-06-01 11:59:00')
    enqueue.script.append(HTTPException(status_code=503, detail='broker down'))
    assert await tick(NOW) == 0
    fresh = await Task.get(t.id)
    assert fresh.next_run_at == '2030-06-01 11:59:00'
    # Broker back: catch-up fire happens on the next tick.
    assert await tick(NOW) == 1


async def test_tick_lost_cas_race_skips(sched_db, enqueue, monkeypatch):
    await _mk(schedule_interval=300, next_run_at='2030-06-01 11:59:00')

    async def zero(*a, **kw):
        return 0
    monkeypatch.setattr(Task, 'update_one', staticmethod(zero))
    assert await tick(NOW) == 0
    assert enqueue.calls == []


async def test_tick_bad_row_does_not_stop_others(sched_db, enqueue):
    await _mk(schedule_interval=300, next_run_at='garbage')
    ok = await _mk(schedule_interval=300, next_run_at='2030-06-01 11:59:00')
    assert await tick(NOW) == 1
    assert enqueue.calls[0].id == ok.id


async def test_tick_unexpected_error_reverts_and_continues(sched_db, enqueue):
    t = await _mk(schedule_interval=300, next_run_at='2030-06-01 11:59:00')
    enqueue.script.append(RuntimeError('boom'))
    assert await tick(NOW) == 0
    fresh = await Task.get(t.id)
    assert fresh.next_run_at == '2030-06-01 11:59:00'  # reverted, will retry
