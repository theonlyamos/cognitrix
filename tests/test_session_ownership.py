import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, jwt_only
from cognitrix.models.api_key import APIKey
from cognitrix.sessions.base import Session


def _context(user_id: str = "user-1", *, api_key: bool = False) -> AuthContext:
    key = None
    if api_key:
        key = APIKey(
            _id="key-1",
            name="test",
            user_id=user_id,
            key_hash="hash",
            prefix="ctx_test",
            scopes=["read", "write", "chat"],
        )
    return AuthContext(user=SimpleNamespace(id=user_id), api_key=key)


def _app(ctx: AuthContext) -> FastAPI:
    from cognitrix.api.routes.sessions import sessions_api

    app = FastAPI()
    app.include_router(sessions_api)
    app.dependency_overrides[crud_scope] = lambda: ctx
    app.dependency_overrides[get_auth_context] = lambda: ctx
    app.dependency_overrides[jwt_only] = lambda: ctx
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json)


@pytest.mark.parametrize("identity_field", ["id", "_id"])
async def test_rest_create_rejects_client_id_before_it_can_overwrite_a_session(
    monkeypatch,
    identity_field,
):
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)

    response = await _request(
        _app(_context("attacker")),
        "POST",
        "/sessions",
        json={
            identity_field: "victim-session",
            "agent_id": "agent-1",
        },
    )

    assert response.status_code == 422
    assert saved == []


async def test_rest_create_rejects_run_transcript_injection(monkeypatch):
    from cognitrix.tasks.run import TaskRun

    saved: list[Session] = []
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        requested_by="owner",
        acl_version=1,
    )

    async def save(session):
        saved.append(session)
        return session

    async def get_run(_run_id):
        return run

    monkeypatch.setattr(Session, "save", save)
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))

    response = await _request(
        _app(_context("owner")),
        "POST",
        "/sessions",
        json={
            "agent_id": "agent-1",
            "run_id": run.id,
            "task_id": run.task_id,
            "step_index": 0,
            "step_title": "Injected",
            "chat": [{"role": "assistant", "content": "forged result"}],
        },
    )

    assert response.status_code == 422
    assert saved == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "task-1"),
        ("step_index", 0),
        ("step_title", "Injected"),
        ("started_at", "2030-01-01 00:00:00"),
        ("completed_at", "2030-01-01 00:01:00"),
        ("pid", "forged-worker"),
    ],
)
async def test_rest_create_rejects_server_authored_execution_fields(
    monkeypatch,
    field,
    value,
):
    saved: list[Session] = []

    async def save(session):
        saved.append(session)
        return session

    monkeypatch.setattr(Session, "save", save)

    response = await _request(
        _app(_context("owner")),
        "POST",
        "/sessions",
        json={"agent_id": "agent-1", field: value},
    )

    assert response.status_code == 422
    assert saved == []


async def test_sqlite_schema_migrates_session_owner_column(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / "session-owner.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()
    await DBMS.Database.query(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, agent_id TEXT, chat TEXT)"
    )

    await _ensure_schema()

    cursor = await DBMS.Database.query("PRAGMA table_info(sessions)")
    rows = cursor.fetchall()
    if hasattr(rows, "__await__"):
        rows = await rows
    assert "user_id" in {row[1] for row in rows}


async def _initialize_ownership_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.session_ownership import SessionOwnership

    db_file = str(tmp_path / 'session-ownership.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await SessionOwnership.create_table()


async def test_concurrent_claim_is_unique_and_legacy_sessions_are_denied(tmp_path):
    from cognitrix.session_ownership import (
        OwnershipConflict,
        OwnershipNotFound,
        SessionOwnership,
        claim_new,
        require_owned,
    )

    await _initialize_ownership_db(tmp_path)

    results = await asyncio.gather(
        claim_new('session-1', 'user-1', 'agent-1'),
        claim_new('session-1', 'user-2', 'agent-1'),
        return_exceptions=True,
    )

    assert sum(isinstance(item, SessionOwnership) for item in results) == 1
    assert sum(isinstance(item, OwnershipConflict) for item in results) == 1
    stored = await SessionOwnership.find({'session_id': 'session-1'})
    assert len(stored) == 1
    winner = stored[0]
    assert winner.user_id in {'user-1', 'user-2'}
    assert await require_owned('session-1', winner.user_id, 'agent-1') == winner

    with pytest.raises(OwnershipNotFound):
        await require_owned('session-1', 'other-user', 'agent-1')
    with pytest.raises(OwnershipNotFound):
        await require_owned('session-1', winner.user_id, 'other-agent')
    # A Session row without a binding is a remote-denied legacy session.
    with pytest.raises(OwnershipNotFound):
        await require_owned('legacy-session', winner.user_id, 'agent-1')


async def test_principal_key_prefers_id_and_owned_lists_are_exact(tmp_path):
    from cognitrix.session_ownership import (
        claim_new,
        owned_session_ids,
        principal_key,
        session_ownerships,
    )

    await _initialize_ownership_db(tmp_path)
    assert principal_key(SimpleNamespace(id='user-id', email='mail@example.com')) == 'user-id'
    assert principal_key(SimpleNamespace(id=None, email='Mail@Example.COM')) == 'mail@example.com'
    with pytest.raises(ValueError):
        principal_key(SimpleNamespace(id=None, email=''))

    await claim_new('u1-a1', 'user-1', 'agent-1')
    await claim_new('u1-a2', 'user-1', 'agent-2')
    await claim_new('u2-a1', 'user-2', 'agent-1')

    assert set(await owned_session_ids('user-1')) == {'u1-a1', 'u1-a2'}
    assert await owned_session_ids('user-1', agent_id='agent-1') == ['u1-a1']
    assert await owned_session_ids('user-2', agent_id='agent-2') == []
    facade_binding = await session_ownerships.require_active_owned(
        'u1-a1', 'user-1', 'agent-1',
    )
    assert facade_binding.session_id == 'u1-a1'


async def test_reservation_ledger_enforces_generation_count_bytes_and_exact_release(tmp_path):
    from cognitrix.session_ownership import (
        MAX_SESSION_BYTES,
        MAX_SESSION_DOCUMENTS,
        OwnershipConflict,
        OwnershipQuotaExceeded,
        adopt_reservation,
        claim_new,
        commit_reservation,
        release_reservation,
        require_owned,
        reserve_intent,
    )

    await _initialize_ownership_db(tmp_path)
    binding = await claim_new('session-1', 'user-1', 'agent-1')

    for index in range(MAX_SESSION_DOCUMENTS):
        await reserve_intent(
            'session-1', 'user-1', 'agent-1',
            generation=binding.generation,
            promotion_token=f'token-{index}',
            size_bytes=1,
            now=100.0,
        )
    with pytest.raises(OwnershipQuotaExceeded):
        await reserve_intent(
            'session-1', 'user-1', 'agent-1',
            generation=binding.generation,
            promotion_token='too-many',
            size_bytes=1,
            now=100.0,
        )

    unchanged = await release_reservation(
        'session-1', 'user-1', 'agent-1', 'wrong-token',
    )
    assert len(unchanged.reservations) == MAX_SESSION_DOCUMENTS
    await release_reservation('session-1', 'user-1', 'agent-1', 'token-0')
    await adopt_reservation(
        'session-1', 'user-1', 'agent-1',
        generation=binding.generation,
        promotion_token='token-1',
        now=100.0,
    )
    await commit_reservation(
        'session-1', 'user-1', 'agent-1',
        generation=binding.generation,
        promotion_token='token-1',
    )
    stored = await require_owned('session-1', 'user-1', 'agent-1')
    assert stored.document_count == 1
    assert stored.document_bytes == 1
    committed = [
        record for record in stored.reservations
        if record['promotion_token'] == 'token-1'
    ]
    assert committed == [{
        'promotion_token': 'token-1',
        'size_bytes': 1,
        'generation': binding.generation,
        'status': 'committed',
    }]

    # A fresh binding can reserve exactly the byte ceiling, but never exceed it.
    bytes_binding = await claim_new('bytes-session', 'user-1', 'agent-1')
    await reserve_intent(
        'bytes-session', 'user-1', 'agent-1',
        generation=bytes_binding.generation,
        promotion_token='all-bytes',
        size_bytes=MAX_SESSION_BYTES,
        now=100.0,
    )
    with pytest.raises(OwnershipQuotaExceeded):
        await reserve_intent(
            'bytes-session', 'user-1', 'agent-1',
            generation=bytes_binding.generation,
            promotion_token='one-more-byte',
            size_bytes=1,
            now=100.0,
        )

    with pytest.raises(OwnershipConflict):
        await reserve_intent(
            'session-1', 'user-1', 'agent-1',
            generation=binding.generation + 1,
            promotion_token='future-generation',
            size_bytes=1,
            now=100.0,
        )


async def test_lifecycle_rotates_generation_blocks_live_lease_and_uses_cas(tmp_path):
    from cognitrix.session_ownership import (
        LifecycleLeaseActive,
        OwnershipConflict,
        OwnershipState,
        abort_lifecycle,
        begin_clear,
        claim_new,
        finish_clear,
        release_reservation,
        require_owned,
        resume_lifecycle,
        reserve_intent,
    )

    await _initialize_ownership_db(tmp_path)
    binding = await claim_new('session-1', 'user-1', 'agent-1')
    await reserve_intent(
        'session-1', 'user-1', 'agent-1',
        generation=binding.generation,
        promotion_token='live',
        size_bytes=10,
        now=100.0,
        ttl_seconds=60.0,
    )
    with pytest.raises(LifecycleLeaseActive):
        await begin_clear('session-1', 'user-1', 'agent-1', now=110.0)

    await release_reservation('session-1', 'user-1', 'agent-1', 'live')
    token = await begin_clear('session-1', 'user-1', 'agent-1', now=110.0)
    assert token.state == OwnershipState.CLEARING
    assert token.generation == binding.generation + 1

    with pytest.raises(OwnershipConflict):
        await reserve_intent(
            'session-1', 'user-1', 'agent-1',
            generation=binding.generation,
            promotion_token='stale',
            size_bytes=1,
            now=110.0,
        )

    active = await abort_lifecycle(token)
    assert active.state == OwnershipState.ACTIVE
    assert active.generation == token.generation

    second = await begin_clear('session-1', 'user-1', 'agent-1', now=120.0)
    resumed = await resume_lifecycle(
        'session-1', 'user-1', 'agent-1', OwnershipState.CLEARING,
    )
    assert resumed == second
    await finish_clear(resumed)
    with pytest.raises(OwnershipConflict):
        await finish_clear(second)
    stored = await require_owned('session-1', 'user-1', 'agent-1')
    assert stored.state == OwnershipState.ACTIVE
    assert stored.document_count == 0 and stored.document_bytes == 0


async def test_committed_document_quota_is_released_only_by_exact_owner(tmp_path):
    from cognitrix.session_ownership import (
        OwnershipConflict,
        OwnershipNotFound,
        adopt_reservation,
        claim_new,
        commit_reservation,
        release_document,
        require_owned,
        reserve_intent,
    )

    await _initialize_ownership_db(tmp_path)
    binding = await claim_new('session-1', 'user-1', 'agent-1')
    await reserve_intent(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token', size_bytes=9, now=100.0,
    )
    await adopt_reservation(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token', now=100.0,
    )
    await commit_reservation(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token',
    )

    with pytest.raises(OwnershipNotFound):
        await release_document(
            'session-1', 'other-user', 'agent-1',
            promotion_token='token', size_bytes=9,
        )
    with pytest.raises(OwnershipConflict):
        await release_document(
            'session-1', 'user-1', 'agent-1',
            promotion_token='token', size_bytes=8,
        )
    released = await release_document(
        'session-1', 'user-1', 'agent-1',
        promotion_token='token', size_bytes=9,
    )
    assert released.document_count == 0 and released.document_bytes == 0
    # A cleanup retry is idempotent: the exact token has already been
    # reconciled, so quota cannot be decremented a second time.
    retried = await release_document(
        'session-1', 'user-1', 'agent-1',
        promotion_token='token', size_bytes=9,
    )
    assert retried.document_count == 0 and retried.document_bytes == 0
    assert (await require_owned('session-1', 'user-1', 'agent-1')).reservations == []


async def test_reconciliation_only_expires_pending_and_delete_binding_is_last_step(tmp_path):
    from cognitrix.session_ownership import (
        LifecycleLeaseActive,
        OwnershipState,
        adopt_reservation,
        begin_delete,
        claim_new,
        finish_delete,
        reconcile_expired_reservations,
        release_reservation,
        require_owned,
        reserve_intent,
    )

    await _initialize_ownership_db(tmp_path)
    binding = await claim_new('session-1', 'user-1', 'agent-1')
    await reserve_intent(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='expired-pending', size_bytes=2, now=100.0, ttl_seconds=1.0,
    )
    await reserve_intent(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='expired-adopting', size_bytes=3, now=100.0, ttl_seconds=1.0,
    )
    await adopt_reservation(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='expired-adopting', now=100.0,
    )

    removed = await reconcile_expired_reservations(
        'session-1', 'user-1', 'agent-1', now=102.0,
    )
    assert removed == 1
    stored = await require_owned('session-1', 'user-1', 'agent-1')
    assert [record['promotion_token'] for record in stored.reservations] == ['expired-adopting']

    # Adoption cannot become destructively ignorable merely because its lease
    # timestamp elapsed; exact rollback/commit reconciliation must resolve it.
    with pytest.raises(LifecycleLeaseActive):
        await begin_delete('session-1', 'user-1', 'agent-1', now=102.0)

    await release_reservation(
        'session-1', 'user-1', 'agent-1', 'expired-adopting',
    )
    lifecycle = await begin_delete('session-1', 'user-1', 'agent-1', now=102.0)
    assert lifecycle.state == OwnershipState.DELETING
    assert await require_owned('session-1', 'user-1', 'agent-1')
    await finish_delete(lifecycle)
    with pytest.raises(Exception) as exc_info:
        await require_owned('session-1', 'user-1', 'agent-1')
    assert exc_info.type.__name__ == 'OwnershipNotFound'


def test_schema_preferred_async_entrypoint_is_owned_by_binding_model():
    from cognitrix.session_ownership import SessionOwnership

    assert '_create_table_async' in SessionOwnership.__dict__


async def test_claim_save_settles_before_cancellation_propagates(tmp_path, monkeypatch):
    from cognitrix.session_ownership import SessionOwnership, claim_new

    await _initialize_ownership_db(tmp_path)
    original_save = SessionOwnership.save
    committed = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def effect_then_wait(self):
        result = await original_save(self)
        committed.set()
        await release.wait()
        finished.set()
        return result

    monkeypatch.setattr(SessionOwnership, 'save', effect_then_wait)
    task = asyncio.create_task(claim_new('session-1', 'user-1', 'agent-1'))
    await committed.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()
    assert (await SessionOwnership.find_one({'session_id': 'session-1'})) is not None


async def test_reserve_cas_settles_and_retry_is_idempotent(tmp_path, monkeypatch):
    from cognitrix.session_ownership import (
        SessionOwnership,
        claim_new,
        require_owned,
        reserve_intent,
    )

    await _initialize_ownership_db(tmp_path)
    binding = await claim_new('session-1', 'user-1', 'agent-1')
    original_update = SessionOwnership.update_one
    committed = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def effect_then_wait(query, values):
        result = await original_update(query, values)
        committed.set()
        await release.wait()
        finished.set()
        return result

    monkeypatch.setattr(SessionOwnership, 'update_one', staticmethod(effect_then_wait))
    task = asyncio.create_task(reserve_intent(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token', size_bytes=7,
    ))
    await committed.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()

    retried = await reserve_intent(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token', size_bytes=7,
    )
    assert [record['promotion_token'] for record in retried.reservations] == ['token']
    assert (await require_owned('session-1', 'user-1', 'agent-1')).version == 1


async def test_commit_cas_settles_and_retry_does_not_double_count(tmp_path, monkeypatch):
    from cognitrix.session_ownership import (
        SessionOwnership,
        adopt_reservation,
        claim_new,
        commit_reservation,
        reserve_intent,
    )

    await _initialize_ownership_db(tmp_path)
    binding = await claim_new('session-1', 'user-1', 'agent-1')
    await reserve_intent(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token', size_bytes=11,
    )
    await adopt_reservation(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token',
    )
    original_update = SessionOwnership.update_one
    committed = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def effect_then_wait(query, values):
        result = await original_update(query, values)
        committed.set()
        await release.wait()
        finished.set()
        return result

    monkeypatch.setattr(SessionOwnership, 'update_one', staticmethod(effect_then_wait))
    task = asyncio.create_task(commit_reservation(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token',
    ))
    await committed.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()

    retried = await commit_reservation(
        'session-1', 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='token',
    )
    assert retried.document_count == 1 and retried.document_bytes == 11


async def test_exact_binding_delete_settles_before_cancellation(tmp_path, monkeypatch):
    from cognitrix.session_ownership import (
        OwnershipState,
        SessionOwnership,
        begin_delete,
        claim_new,
        finish_delete,
    )

    await _initialize_ownership_db(tmp_path)
    await claim_new('session-1', 'user-1', 'agent-1')
    token = await begin_delete('session-1', 'user-1', 'agent-1')
    assert token.state == OwnershipState.DELETING
    original_delete = SessionOwnership.delete_many
    committed = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def effect_then_wait(query, cascade=False):
        result = await original_delete(query, cascade=cascade)
        committed.set()
        await release.wait()
        finished.set()
        return result

    monkeypatch.setattr(SessionOwnership, 'delete_many', staticmethod(effect_then_wait))
    task = asyncio.create_task(finish_delete(token))
    await committed.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert await SessionOwnership.find_one({'session_id': 'session-1'}) is None


async def test_reconciliation_query_is_indexed_filtered_and_hard_bounded(tmp_path):
    from odbms import DBMS

    from cognitrix.session_ownership import (
        MAX_RECONCILIATION_BINDINGS,
        SessionOwnership,
        claim_new,
        reconciliation_bindings,
        reserve_intent,
        session_ownerships,
    )

    await _initialize_ownership_db(tmp_path)
    for index in range(4):
        binding = await claim_new(f'session-{index}', 'user-1', 'agent-1')
        if index < 3:
            await reserve_intent(
                binding.session_id, binding.user_id, binding.agent_id,
                generation=binding.generation,
                promotion_token=f'token-{index}',
                size_bytes=1,
            )

    rows = await reconciliation_bindings(limit=2)
    assert len(rows) == 2
    assert all(row.reservations for row in rows)
    assert len(await session_ownerships.reconciliation_bindings(limit=0)) == 1
    assert len(await reconciliation_bindings(limit=MAX_RECONCILIATION_BINDINGS + 999)) == 3

    cursor = await DBMS.Database.query(
        f'PRAGMA index_list({SessionOwnership.table_name()})'
    )
    indexes = cursor.fetchall()
    assert any('reconciliation' in str(row[1]) for row in indexes)


async def test_mongodb_startup_creates_all_ownership_indexes():
    from cognitrix.session_ownership import _ensure_critical_indexes

    calls = []

    class Collection:
        async def create_index(self, keys, **options):
            calls.append((keys, options))
            return options['name']

    collection = Collection()
    database = SimpleNamespace(
        dbms='mongodb',
        db={'session_ownerships': collection},
    )

    await _ensure_critical_indexes(database, 'session_ownerships')

    assert calls == [
        (
            [('session_id', 1)],
            {
                'name': 'ux_session_ownerships_session_id',
                'unique': True,
            },
        ),
        (
            [('user_id', 1), ('agent_id', 1)],
            {'name': 'ix_session_ownerships_owner_agent'},
        ),
        (
            [('updated_at', 1)],
            {
                'name': 'ix_session_ownerships_reconciliation',
                'partialFilterExpression': {
                    'reservations.0': {'$exists': True},
                },
            },
        ),
    ]


async def test_ownership_schema_accepts_synchronous_odbms_base(monkeypatch):
    from odbms import DBMS, Model

    from cognitrix.session_ownership import SessionOwnership

    base_calls = []
    statements = []

    async def create_base_async(cls):
        base_calls.append(cls)

    def create_base(_cls):
        raise AssertionError('ODBMS sync scheduling wrapper must be bypassed')

    async def query(statement):
        statements.append(statement)

    monkeypatch.setattr(
        Model,
        '_create_table_async',
        classmethod(create_base_async),
        raising=False,
    )
    monkeypatch.setattr(Model, 'create_table', classmethod(create_base))
    monkeypatch.setattr(
        DBMS,
        'Database',
        SimpleNamespace(dbms='sqlite', query=query),
    )

    await SessionOwnership.create_table()

    assert base_calls == [SessionOwnership]
    assert any(
        statement.startswith('CREATE UNIQUE INDEX IF NOT EXISTS')
        for statement in statements
    )


async def test_mysql_startup_creates_indexes_once_after_information_schema_checks():
    from cognitrix.session_ownership import _ensure_critical_indexes

    existing = set()
    statements = []

    class AsyncContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    class Cursor:
        row = None

        async def execute(self, statement, params=()):
            statements.append((statement, params))
            normalized = statement.strip().upper()
            if normalized.startswith('SELECT'):
                self.row = (1,) if params[1] in existing else None
            elif normalized.startswith('CREATE'):
                marker = normalized.index(' INDEX ') + len(' INDEX ')
                remainder = statement[marker:]
                index_name = remainder.split(None, 1)[0].strip('`')
                existing.add(index_name)

        async def fetchone(self):
            return self.row

    class Connection:
        def cursor(self):
            return AsyncContext(Cursor())

    class Pool:
        def acquire(self):
            return AsyncContext(Connection())

    database = SimpleNamespace(dbms='mysql', _pool=Pool())
    await _ensure_critical_indexes(database, 'session_ownerships')
    await _ensure_critical_indexes(database, 'session_ownerships')

    creates = [
        statement for statement, _ in statements
        if statement.strip().upper().startswith('CREATE')
    ]
    assert creates == [
        'CREATE UNIQUE INDEX `ux_session_ownerships_session_id` '
        'ON `session_ownerships` (`session_id`(255))',
        'CREATE INDEX `ix_session_ownerships_owner_agent` '
        'ON `session_ownerships` (`user_id`(255), `agent_id`(255))',
        'CREATE INDEX `ix_session_ownerships_reconciliation` '
        'ON `session_ownerships` (`updated_at`)',
    ]


async def test_unknown_database_backend_fails_closed_for_ownership_indexes():
    from cognitrix.session_ownership import _ensure_critical_indexes

    with pytest.raises(RuntimeError, match='cannot guarantee SessionOwnership indexes'):
        await _ensure_critical_indexes(
            SimpleNamespace(dbms='unknown'),
            'session_ownerships',
        )
