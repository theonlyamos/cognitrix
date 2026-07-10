"""Task schedule fields, next-run math, and scheduler tick behavior."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from cognitrix.api.routes.tasks import ScheduleToggle, save_task, toggle_schedule
from cognitrix.common.security import AuthContext
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.scheduler import compute_next_run, normalize_schedule_at, tick, validate_schedule

NOW = datetime(2030, 6, 1, 12, 0, 0)


def test_schedule_datetime_normalizer_is_a_scheduler_api():
    """Public scheduler helpers must not be removable as unused imports."""
    assert normalize_schedule_at.__module__ == 'cognitrix.tasks.scheduler'


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


def test_validate_impossible_cron():
    """Well-formed but unsatisfiable (Feb 30) must be rejected, not passed
    through to a get_next() that raises later."""
    assert 'invalid cron' in validate_schedule(_task(schedule_cron='0 0 30 2 *'), respecified=True)


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
    from cognitrix.tasks.run import TaskRun
    for model in (Task, TaskRun):
        create = getattr(model, '_create_table_async', None) or model.create_table
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


# --- save_task / toggle routes -------------------------------------------------

def _jwt_ctx():
    return AuthContext(user=SimpleNamespace(id='u1'), api_key=None)


def _key_ctx(scopes, allowed_agents=()):
    from cognitrix.models.api_key import APIKey
    key = APIKey(name='k', user_id='u1', key_hash='h', prefix='p',
                 scopes=list(scopes), allowed_agents=list(allowed_agents),
                 webhook_secret='s')
    key.id = 'key1'
    return AuthContext(user=SimpleNamespace(id='u1'), api_key=key)


async def _save(task, ctx):
    return await save_task(None, task, BackgroundTasks(), ctx)


async def test_assignment_updates_only_ownership_fields(sched_db):
    import cognitrix.api.routes.tasks as task_routes

    stored = Task(
        title='scheduled',
        description='keep me',
        status=TaskStatus.COMPLETED,
        done=True,
        autostart=True,
        assigned_agents=['agent-old'],
        team_id='team-old',
        results=['keep result'],
        pid='worker-1',
        schedule_interval=300,
        next_run_at='2030-06-01 12:05:00',
        schedule_enabled=True,
    )
    await stored.save()

    body = SimpleNamespace(assigned_agents=['agent-new'], team_id='team-new')
    result = await task_routes.assign_task(stored.id, body, _jwt_ctx())
    fresh = await Task.get(stored.id)

    assert result['assigned_agents'] == ['agent-new']
    assert result['team_id'] == 'team-new'
    assert fresh.title == 'scheduled'
    assert fresh.description == 'keep me'
    assert fresh.status == TaskStatus.COMPLETED
    assert fresh.done is True
    assert fresh.autostart is True
    assert fresh.results == ['keep result']
    assert fresh.pid == 'worker-1'
    assert fresh.schedule_interval == 300
    assert fresh.next_run_at == '2030-06-01 12:05:00'
    assert fresh.schedule_enabled is True


async def test_save_new_schedule_defaults_enabled(sched_db):
    t = Task(title='t', description='d', schedule_interval=300)
    data = await _save(t, _jwt_ctx())
    assert data['schedule_enabled'] is True
    assert data['next_run_at'] is not None


async def test_save_title_edit_preserves_schedule_and_callbacks(sched_db):
    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:05:00', schedule_enabled=True,
                  callback_url='https://hooks.example.com/x', callback_key_id='key1')
    await stored.save()

    edit = Task(id=stored.id, title='renamed', description='d')
    await _save(edit, _jwt_ctx())
    fresh = await Task.get(stored.id)
    assert fresh.title == 'renamed'
    assert fresh.schedule_interval == 300
    assert fresh.next_run_at == '2030-06-01 12:05:00'  # NOT recomputed
    assert fresh.schedule_enabled is True
    assert fresh.callback_url == 'https://hooks.example.com/x'
    assert fresh.callback_key_id == 'key1'


async def test_save_respec_switches_type(sched_db):
    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:05:00', schedule_enabled=True)
    await stored.save()

    edit = Task(id=stored.id, title='t', description='d', schedule_cron='*/10 * * * *')
    await _save(edit, _jwt_ctx())
    fresh = await Task.get(stored.id)
    assert fresh.schedule_interval is None
    assert fresh.schedule_cron == '*/10 * * * *'
    assert fresh.next_run_at != '2030-06-01 12:05:00'


async def test_save_normalizes_offset_iso(sched_db):
    t = Task(title='t', description='d', schedule_at='2031-06-01T12:00:00+02:00')
    data = await _save(t, _jwt_ctx())
    assert data['schedule_at'] == '2031-06-01 10:00:00'
    assert data['next_run_at'] == '2031-06-01 10:00:00'


async def test_save_past_oneshot_422(sched_db):
    t = Task(title='t', description='d', schedule_at='2020-01-01 00:00:00')
    with pytest.raises(HTTPException) as exc:
        await _save(t, _jwt_ctx())
    assert exc.value.status_code == 422


async def test_save_clearing_schedule_disables(sched_db):
    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:05:00', schedule_enabled=True)
    await stored.save()
    edit = Task(id=stored.id, title='t', description='d', schedule_interval=None)
    await _save(edit, _jwt_ctx())
    fresh = await Task.get(stored.id)
    assert fresh.schedule_enabled is False and fresh.next_run_at is None


async def test_save_lone_enabled_toggles_not_wipes(sched_db):
    """A payload with schedule_enabled but no type field is a toggle, not a
    respecification — it must NOT wipe the stored schedule type."""
    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:05:00', schedule_enabled=True)
    await stored.save()

    # Pause via the flag alone.
    await _save(Task(id=stored.id, title='t', description='d', schedule_enabled=False), _jwt_ctx())
    paused = await Task.get(stored.id)
    assert paused.schedule_interval == 300  # NOT wiped
    assert paused.schedule_enabled is False and paused.next_run_at is None

    # Resume via the flag alone: type preserved, next_run_at recomputed.
    await _save(Task(id=stored.id, title='t', description='d', schedule_enabled=True), _jwt_ctx())
    resumed = await Task.get(stored.id)
    assert resumed.schedule_interval == 300
    assert resumed.schedule_enabled is True and resumed.next_run_at is not None


async def test_save_lone_enable_write_key_403(sched_db):
    """Enabling a stored (disabled) schedule via the flag arms execution —
    an API key still needs run scope for it."""
    stored = Task(title='t', description='d', schedule_interval=300, schedule_enabled=False)
    await stored.save()
    with pytest.raises(HTTPException) as exc:
        await _save(Task(id=stored.id, title='t', description='d', schedule_enabled=True), _key_ctx(['write']))
    assert exc.value.status_code == 403


async def test_save_impossible_cron_422(sched_db):
    with pytest.raises(HTTPException) as exc:
        await _save(Task(title='t', description='d', schedule_cron='0 0 30 2 *'), _jwt_ctx())
    assert exc.value.status_code == 422


async def test_save_write_key_403_on_respec(sched_db):
    t = Task(title='t', description='d', schedule_interval=300)
    with pytest.raises(HTTPException) as exc:
        await _save(t, _key_ctx(['write']))
    assert exc.value.status_code == 403


async def test_save_write_key_403_editing_enabled_task(sched_db):
    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:05:00', schedule_enabled=True)
    await stored.save()
    edit = Task(id=stored.id, title='renamed', description='d')
    with pytest.raises(HTTPException) as exc:
        await _save(edit, _key_ctx(['write']))
    assert exc.value.status_code == 403


async def test_save_run_key_allowed(sched_db):
    t = Task(title='t', description='d', schedule_interval=300)
    data = await _save(t, _key_ctx(['write', 'run']))
    assert data['schedule_enabled'] is True


async def test_toggle_pause_and_resume(sched_db):
    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:05:00', schedule_enabled=True)
    await stored.save()

    data = await toggle_schedule(stored.id, ScheduleToggle(enabled=False), _jwt_ctx())
    assert data['schedule_enabled'] is False and data['next_run_at'] is None

    data = await toggle_schedule(stored.id, ScheduleToggle(enabled=True), _jwt_ctx())
    assert data['schedule_enabled'] is True
    assert data['next_run_at'] is not None and data['next_run_at'] != '2030-06-01 12:05:00'


async def test_toggle_resume_past_oneshot_422(sched_db):
    stored = Task(title='t', description='d', schedule_at='2020-01-01 00:00:00',
                  schedule_enabled=False)
    await stored.save()
    with pytest.raises(HTTPException) as exc:
        await toggle_schedule(stored.id, ScheduleToggle(enabled=True), _jwt_ctx())
    assert exc.value.status_code == 422


async def test_toggle_enable_without_schedule_422(sched_db):
    stored = Task(title='t', description='d')
    await stored.save()
    with pytest.raises(HTTPException) as exc:
        await toggle_schedule(stored.id, ScheduleToggle(enabled=True), _jwt_ctx())
    assert exc.value.status_code == 422


async def test_toggle_enable_write_key_403(sched_db):
    stored = Task(title='t', description='d', schedule_interval=300)
    await stored.save()
    with pytest.raises(HTTPException) as exc:
        await toggle_schedule(stored.id, ScheduleToggle(enabled=True), _key_ctx(['write']))
    assert exc.value.status_code == 403


async def test_enqueue_partial_write_preserves_concurrent_claim(sched_db, monkeypatch):
    """_enqueue_task_start must not full-row-save: a concurrent scheduler
    claim (next_run_at advance) written between fetch and start survives."""
    import cognitrix.api.routes.tasks as task_routes

    stored = Task(title='t', description='d', schedule_interval=300,
                  next_run_at='2030-06-01 12:00:00', schedule_enabled=True)
    await stored.save()

    monkeypatch.setattr(task_routes, 'ensure_local_worker', lambda: True)
    monkeypatch.setattr(task_routes, 'broker_available', lambda: True)
    monkeypatch.setattr(task_routes.run_task, 'apply_async',
                        lambda *a, **kw: SimpleNamespace(id='celery-1'))

    # Another writer advances the schedule after our instance was fetched.
    await Task.update_one({'id': stored.id}, {'next_run_at': '2030-06-01 12:05:00'})

    await task_routes._enqueue_task_start(stored)
    fresh = await Task.get(stored.id)
    assert fresh.status == TaskStatus.IN_PROGRESS and fresh.pid == 'celery-1'
    assert fresh.next_run_at == '2030-06-01 12:05:00'  # claim survived
