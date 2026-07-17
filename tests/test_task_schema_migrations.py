from __future__ import annotations

import inspect

import pytest


async def _rows(cursor):
    value = cursor.fetchall()
    return await value if inspect.isawaitable(value) else value


@pytest.mark.asyncio
async def test_sqlite_reconciles_duplicate_event_sequences_without_losing_conflicts(
    tmp_path,
):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / "task-migration.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    await DBMS.Database.query(
        "CREATE TABLE taskruns ("
        "id TEXT PRIMARY KEY, task_id TEXT, status TEXT, plan TEXT, "
        "result TEXT, error TEXT, started_at TEXT, completed_at TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    await DBMS.Database.query(
        "INSERT INTO taskruns (id, task_id, status, plan) VALUES "
        "('run-a', 'task-a', 'running', '[]'), "
        "('run-empty', 'task-empty', 'queued', '[]')"
    )
    await DBMS.Database.query(
        "CREATE TABLE taskrunevents ("
        "id TEXT, run_id TEXT, session_id TEXT, step_index INTEGER, "
        "sequence INTEGER, kind TEXT, agent_name TEXT, data TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    events = (
        ("event-a", 1, '{"value":"same"}'),
        ("event-b", 1, '{"value":"same"}'),
        ("event-c", 1, '{"value":"conflict"}'),
        ("event-d", 5, '{"value":"existing-max"}'),
    )
    for event_id, sequence, data in events:
        await DBMS.Database.query(
            "INSERT INTO taskrunevents "
            "(id, run_id, session_id, step_index, sequence, kind, agent_name, "
            "data, created_at, updated_at) VALUES "
            f"('{event_id}', 'run-a', 'session-1', 0, {sequence}, "
            f"'text_delta', 'Agent', '{data}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

    await _ensure_schema()
    await _ensure_schema()

    cursor = await DBMS.Database.query(
        "SELECT id, sequence, data FROM taskrunevents "
        "WHERE run_id = 'run-a' ORDER BY sequence, id"
    )
    stored = [tuple(row) for row in await _rows(cursor)]

    assert stored == [
        ("event-a", 1, '{"value":"same"}'),
        ("event-d", 5, '{"value":"existing-max"}'),
        ("event-c", 6, '{"value":"conflict"}'),
    ]

    counters_cursor = await DBMS.Database.query(
        "SELECT id, lease_generation, version, next_event_sequence "
        "FROM taskruns ORDER BY id"
    )
    counters = [tuple(row) for row in await _rows(counters_cursor)]
    assert counters == [
        ("run-a", 0, 0, 6),
        ("run-empty", 0, 0, 0),
    ]


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _RelationalRecorder:
    def __init__(self, dbms: str, *, valid_event_index: bool = True):
        self.dbms = dbms
        self.valid_event_index = valid_event_index
        self.calls: list[tuple[str, dict | None]] = []

    async def query(self, statement, params=None):
        self.calls.append((statement, params))
        lowered = statement.lower()
        if "pg_index" in lowered:
            unique = self.valid_event_index
            columns = ["run_id", "sequence"] if unique else ["sequence", "run_id"]
            return _Cursor([(unique, columns)])
        if "information_schema.statistics" in lowered:
            if self.valid_event_index:
                return _Cursor([(0, 1, "run_id"), (0, 2, "sequence")])
            return _Cursor([(1, 1, "sequence"), (1, 2, "run_id")])
        return _Cursor([])


@pytest.mark.asyncio
async def test_relational_catalog_verification_reads_inside_odbms_pool_lease():
    from cognitrix.config import _verify_relational_event_index

    calls = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement, params):
            calls.append((statement, params))

        async def fetchall(self):
            return [(True, ["run_id", "sequence"])]

    class Connection:
        def cursor(self):
            return Cursor()

    class Lease:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *args):
            return None

    class Pool:
        def acquire(self):
            return Lease()

    class Database:
        dbms = "postgresql"
        _pool = Pool()

        @staticmethod
        async def query(*_args, **_kwargs):
            raise AssertionError("catalog query escaped the pool lease")

    await _verify_relational_event_index(Database, "postgresql")

    assert len(calls) == 1
    assert "pg_index" in calls[0][0].lower()


@pytest.mark.parametrize("dbms", ["postgresql", "mysql"])
@pytest.mark.asyncio
async def test_relational_migration_backfills_counters_and_verifies_event_index(dbms):
    from cognitrix.config import _migrate_relational_task_schema

    database = _RelationalRecorder(dbms)

    await _migrate_relational_task_schema(database)

    sql = "\n".join(statement for statement, _params in database.calls).lower()
    assert "alter table taskruns" in sql and "authority_kind" in sql
    assert "alter table taskruns" in sql and "authority_id" in sql
    assert "alter table taskruns" in sql and "acl_version" in sql
    assert "alter table artifacts" in sql and "run_id" in sql
    assert "alter table sessions" in sql and "user_id" in sql
    assert "alter table taskrunheads" in sql and "deleted_at" in sql
    assert "alter table tasks" in sql and "schedule_requested_by" in sql
    assert "alter table tasks" in sql and "schedule_authority_kind" in sql
    assert "alter table tasks" in sql and "schedule_authority_id" in sql
    assert "lease_generation = coalesce(lease_generation, 0)" in sql
    assert "version = coalesce(version, 0)" in sql
    assert "next_event_sequence" in sql
    assert "select max(sequence) from taskrunevents" in sql
    assert "row_number() over" in sql
    assert "session_id" in sql and "step_index" in sql and "data" in sql
    if dbms == "postgresql":
        assert "indnkeyatts = 2" in sql and "indnatts = 2" in sql
    else:
        assert "binary data" in sql
        assert "sub_part is null" in sql
    assert any(
        ("pg_index" in statement.lower())
        or ("information_schema.statistics" in statement.lower())
        for statement, _params in database.calls
    )


@pytest.mark.parametrize("dbms", ["postgresql", "mysql"])
@pytest.mark.asyncio
async def test_relational_migration_fails_when_named_event_index_has_wrong_shape(dbms):
    from cognitrix.config import _migrate_relational_task_schema

    database = _RelationalRecorder(dbms, valid_event_index=False)

    with pytest.raises(RuntimeError, match="exact unique index"):
        await _migrate_relational_task_schema(database)
