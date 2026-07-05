"""Task schedule fields, next-run math, and scheduler tick behavior."""

import pytest

from cognitrix.tasks.base import Task


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
