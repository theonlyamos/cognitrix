"""Durable remote ownership and lifecycle control for chat sessions.

``Session`` intentionally remains the execution/history model.  This module is
the authorization boundary: a remote caller must have a binding before code
loads the corresponding Session row.  Legacy Session rows without a binding
therefore fail closed.

Persistence rule: ``save()`` is used only for the initial claim.  Every later
mutation is a partial, version-guarded ``update_one`` so concurrent workers
cannot clobber lifecycle state or quota reservations.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any

from odbms import DBMS, Model
from pydantic import Field, field_validator

MAX_SESSION_DOCUMENTS = 20
MAX_SESSION_BYTES = 100 * 1024 * 1024
DEFAULT_RESERVATION_TTL_SECONDS = 300.0
DEFAULT_RECONCILIATION_BINDINGS = 50
MAX_RECONCILIATION_BINDINGS = 100
MAX_OWNERSHIP_IDENTIFIER_LENGTH = 255


def _safe_identifier(value: str) -> str:
    normalized = str(value or '').strip()
    if not normalized or not all(
        character.isalnum() or character == '_'
        for character in normalized
    ):
        raise RuntimeError('Unsafe database identifier for SessionOwnership')
    return normalized


async def _mysql_index_exists(database, table: str, index_name: str) -> bool:
    pool = getattr(database, '_pool', None)
    if pool is None:
        raise RuntimeError('MySQL connection pool is unavailable')
    statement = (
        'SELECT 1 FROM information_schema.statistics '
        'WHERE table_schema = DATABASE() AND table_name = %s '
        'AND index_name = %s LIMIT 1'
    )
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(statement, (table, index_name))
            return await cursor.fetchone() is not None


async def _create_mysql_index(database, statement: str) -> None:
    pool = getattr(database, '_pool', None)
    if pool is None:
        raise RuntimeError('MySQL connection pool is unavailable')
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(statement)


async def _ensure_mysql_index(
    database,
    table: str,
    index_name: str,
    statement: str,
) -> None:
    if await _mysql_index_exists(database, table, index_name):
        return
    try:
        await _create_mysql_index(database, statement)
    except Exception:
        # Two workers can race at startup.  Suppress only a race that left the
        # exact required index visible; every other DDL failure remains fatal.
        if await _mysql_index_exists(database, table, index_name):
            return
        raise
    if not await _mysql_index_exists(database, table, index_name):
        raise RuntimeError(f'MySQL did not create required index {index_name}')


async def _ensure_critical_indexes(database, table: str) -> None:
    """Create every index that makes ownership enforcement fail closed."""
    table = _safe_identifier(table)
    unique_name = _safe_identifier(f'ux_{table}_session_id')
    owner_name = _safe_identifier(f'ix_{table}_owner_agent')
    reconciliation_name = _safe_identifier(f'ix_{table}_reconciliation')
    dbms = str(getattr(database, 'dbms', '') or '').lower()

    if dbms in {'sqlite', 'postgresql'}:
        await database.query(
            f'CREATE UNIQUE INDEX IF NOT EXISTS {unique_name} '
            f'ON {table} (session_id)'
        )
        await database.query(
            f'CREATE INDEX IF NOT EXISTS {owner_name} '
            f'ON {table} (user_id, agent_id)'
        )
        await database.query(
            f'CREATE INDEX IF NOT EXISTS {reconciliation_name} '
            f"ON {table} (updated_at) WHERE reservations IS NOT NULL "
            f"AND reservations <> '[]'"
        )
        return

    if dbms == 'mongodb':
        mongo_database = getattr(database, 'db', None)
        if mongo_database is None:
            raise RuntimeError(
                'cannot guarantee SessionOwnership indexes: MongoDB is unavailable'
            )
        collection = mongo_database[table]
        await collection.create_index(
            [('session_id', 1)],
            name=unique_name,
            unique=True,
        )
        await collection.create_index(
            [('user_id', 1), ('agent_id', 1)],
            name=owner_name,
        )
        await collection.create_index(
            [('updated_at', 1)],
            name=reconciliation_name,
            partialFilterExpression={
                'reservations.0': {'$exists': True},
            },
        )
        return

    if dbms == 'mysql':
        # ODBMS maps str fields to TEXT on MySQL.  Prefixing the indexes keeps
        # the DDL valid; model validation bounds identifiers to the same 255
        # characters, so the unique prefix covers the complete session id.
        definitions = (
            (
                unique_name,
                f'CREATE UNIQUE INDEX `{unique_name}` ON `{table}` '
                '(`session_id`(255))',
            ),
            (
                owner_name,
                f'CREATE INDEX `{owner_name}` ON `{table}` '
                '(`user_id`(255), `agent_id`(255))',
            ),
            (
                reconciliation_name,
                f'CREATE INDEX `{reconciliation_name}` ON `{table}` '
                '(`updated_at`)',
            ),
        )
        try:
            for index_name, statement in definitions:
                await _ensure_mysql_index(
                    database,
                    table,
                    index_name,
                    statement,
                )
        except Exception as error:
            raise RuntimeError(
                'cannot guarantee SessionOwnership indexes for mysql'
            ) from error
        return

    raise RuntimeError(
        f'cannot guarantee SessionOwnership indexes for backend {dbms or "unset"}'
    )


class OwnershipState(str, Enum):
    ACTIVE = 'active'
    CLEARING = 'clearing'
    DELETING = 'deleting'


class ReservationStatus(str, Enum):
    PENDING = 'pending'
    ADOPTING = 'adopting'
    COMMITTED = 'committed'


class OwnershipError(RuntimeError):
    """Base class for ownership failures."""


class OwnershipNotFound(OwnershipError):
    """Missing, unbound, or foreign sessions share this failure."""

    def __init__(self):
        super().__init__('Session not found')


class OwnershipConflict(OwnershipError):
    """The requested mutation lost a CAS race or conflicts with current state."""


class OwnershipQuotaExceeded(OwnershipConflict):
    """A document reservation would exceed the per-session quota."""


class LifecycleLeaseActive(OwnershipConflict):
    """A clear/delete cannot start while a promotion lease is live."""


class ReservationNotFound(OwnershipConflict):
    """The exact promotion token does not exist on this binding."""


class SessionOwnership(Model):
    """One durable, uniquely indexed owner binding per Session id."""

    session_id: str
    user_id: str
    agent_id: str
    state: OwnershipState = OwnershipState.ACTIVE
    generation: int = 0
    version: int = 0
    document_count: int = 0
    document_bytes: int = 0
    reservations: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator('session_id', 'user_id', 'agent_id', mode='before')
    @classmethod
    def _nonempty_identifier(cls, value: Any) -> str:
        normalized = str(value or '').strip()
        if not normalized:
            raise ValueError('ownership identifiers must be non-empty')
        if len(normalized) > MAX_OWNERSHIP_IDENTIFIER_LENGTH:
            raise ValueError(
                'ownership identifiers must be at most '
                f'{MAX_OWNERSHIP_IDENTIFIER_LENGTH} characters'
            )
        return normalized

    @field_validator('generation', 'version', 'document_count', 'document_bytes', mode='before')
    @classmethod
    def _nonnegative_integer(cls, value: Any) -> int:
        normalized = int(value or 0)
        if normalized < 0:
            raise ValueError('ownership counters must be non-negative')
        return normalized

    @field_validator('reservations', mode='before')
    @classmethod
    def _coerce_reservations(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError('reservations must be a list')
        return [dict(record) for record in value]

    @classmethod
    async def create_table(cls):
        """Create the model table plus its authorization-critical indexes.

        ODBMS does not express unique fields.  The session-id index is the
        cross-process claim boundary; the in-process lock below is only a
        contention optimization.
        """
        await Model.create_table.__func__(cls)
        database = DBMS.Database
        if database is None:
            raise RuntimeError('Database not initialized')
        table = cls.table_name()
        dbms = getattr(database, 'dbms', '')
        await _ensure_critical_indexes(database, table)
        if dbms == 'sqlite':
            invalid = (
                'NEW.session_id IS NULL OR length(trim(NEW.session_id)) = 0 OR '
                'NEW.user_id IS NULL OR length(trim(NEW.user_id)) = 0 OR '
                'NEW.agent_id IS NULL OR length(trim(NEW.agent_id)) = 0'
            )
            await database.query(
                f'''CREATE TRIGGER IF NOT EXISTS validate_{table}_insert
                    BEFORE INSERT ON {table}
                    WHEN {invalid}
                    BEGIN
                        SELECT RAISE(ABORT, 'invalid session ownership binding');
                    END'''
            )
            await database.query(
                f'''CREATE TRIGGER IF NOT EXISTS validate_{table}_update
                    BEFORE UPDATE ON {table}
                    WHEN {invalid}
                    BEGIN
                        SELECT RAISE(ABORT, 'invalid session ownership binding');
                    END'''
            )

    @classmethod
    async def _create_table_async(cls):
        """Preferred ODBMS schema hook; keep custom indexes on every version."""
        await cls.create_table()


@dataclass(frozen=True)
class LifecycleToken:
    binding_id: str
    session_id: str
    user_id: str
    agent_id: str
    state: OwnershipState
    generation: int
    version: int


_claim_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_mutation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


async def _settle_mutation(operation):
    """Join a database mutation before propagating caller cancellation."""
    mutation = asyncio.create_task(operation)
    try:
        return await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        result = mutation.result()
        raise cancelled


def _lock_for(registry: weakref.WeakValueDictionary[str, asyncio.Lock], session_id: str) -> asyncio.Lock:
    lock = registry.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        registry[session_id] = lock
    return lock


def _identifier(value: Any, label: str) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{label} must be non-empty')
    return normalized


def principal_key(user: Any) -> str:
    """Stable remote principal: persistent user id, with email fallback."""
    user_id = str(getattr(user, 'id', '') or '').strip()
    if user_id:
        return user_id
    email = str(getattr(user, 'email', '') or '').strip().lower()
    if email:
        return email
    raise ValueError('Authenticated user has no stable identity')


async def claim_new(session_id: str, user_id: str, agent_id: str) -> SessionOwnership:
    """Claim an unbound, freshly-created session exactly once."""
    session_id = _identifier(session_id, 'session_id')
    user_id = _identifier(user_id, 'user_id')
    agent_id = _identifier(agent_id, 'agent_id')
    async with _lock_for(_claim_locks, session_id):
        if await SessionOwnership.find_one({'session_id': session_id}) is not None:
            raise OwnershipConflict('Session is already claimed')
        binding = SessionOwnership(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        try:
            await _settle_mutation(binding.save())
        except Exception as exc:
            # A unique-index loser is deliberately indistinguishable from a
            # claim observed before INSERT.  Re-read first so unrelated DB
            # failures retain their original diagnostics.
            if await SessionOwnership.find_one({'session_id': session_id}) is not None:
                raise OwnershipConflict('Session is already claimed') from exc
            raise
        stored = await SessionOwnership.find_one({'session_id': session_id})
        if stored is None:
            raise OwnershipConflict('Session claim did not persist')
        return stored


async def discard_fresh_claim(session_id: str, user_id: str, agent_id: str) -> None:
    """Compensate a failed create flow without deleting an established binding."""
    async with _lock_for(_mutation_locks, session_id):
        try:
            binding = await require_owned(session_id, user_id, agent_id)
        except OwnershipNotFound:
            return
        if (
            binding.state != OwnershipState.ACTIVE
            or binding.generation != 0
            or binding.version != 0
            or binding.document_count != 0
            or binding.document_bytes != 0
            or binding.reservations
        ):
            raise OwnershipConflict('Session claim is no longer fresh')
        deleted = await _settle_mutation(SessionOwnership.delete_many({
            'id': str(binding.id),
            'session_id': binding.session_id,
            'user_id': binding.user_id,
            'agent_id': binding.agent_id,
            'state': OwnershipState.ACTIVE.value,
            'generation': 0,
            'version': 0,
        }))
        if deleted != 1:
            raise OwnershipConflict('Session claim changed concurrently')


async def require_owned(
    session_id: str,
    user_id: str,
    agent_id: str | None = None,
) -> SessionOwnership:
    """Return an exact binding or the generic missing/foreign failure."""
    session_id = _identifier(session_id, 'session_id')
    user_id = _identifier(user_id, 'user_id')
    expected_agent = _identifier(agent_id, 'agent_id') if agent_id is not None else None
    binding = await SessionOwnership.find_one({'session_id': session_id})
    if (
        binding is None
        or binding.user_id != user_id
        or (expected_agent is not None and binding.agent_id != expected_agent)
    ):
        raise OwnershipNotFound()
    return binding


async def require_active_owned(
    session_id: str,
    user_id: str,
    agent_id: str | None = None,
) -> SessionOwnership:
    binding = await require_owned(session_id, user_id, agent_id)
    if binding.state != OwnershipState.ACTIVE:
        raise OwnershipConflict('Session lifecycle transition is in progress')
    return binding


async def owned_session_ids(user_id: str, agent_id: str | None = None) -> list[str]:
    user_id = _identifier(user_id, 'user_id')
    query = {'user_id': user_id}
    if agent_id is not None:
        query['agent_id'] = _identifier(agent_id, 'agent_id')
    bindings = await SessionOwnership.find(query)
    return sorted(
        binding.session_id
        for binding in bindings
        if binding.state == OwnershipState.ACTIVE
    )


async def reconciliation_bindings(
    limit: int = DEFAULT_RECONCILIATION_BINDINGS,
) -> list[SessionOwnership]:
    """Return a hard-bounded oldest-first page with non-empty ledgers."""
    try:
        bounded = int(limit)
    except (TypeError, ValueError):
        bounded = DEFAULT_RECONCILIATION_BINDINGS
    bounded = max(1, min(bounded, MAX_RECONCILIATION_BINDINGS))
    database = DBMS.Database
    if database is None:
        raise RuntimeError('Database not initialized')
    table = SessionOwnership.table_name()
    if getattr(database, 'dbms', '') == 'mongodb':
        raw_rows = await database.find(
            table,
            {'reservations': {'$ne': []}},
            limit=bounded,
            sort=[('updated_at', 1)],
        )
    else:
        # bounded is an integer clamped above, so embedding LIMIT is safe and
        # works across the SQLite/Postgres/MySQL adapters' varying param APIs.
        result = await database.query(
            f"SELECT * FROM {table} "
            f"WHERE reservations IS NOT NULL AND reservations <> '[]' "
            f"ORDER BY updated_at ASC LIMIT {bounded}"
        )
        raw_rows = result.fetchall() if hasattr(result, 'fetchall') else (result or [])
    return [
        SessionOwnership(**SessionOwnership.normalise(dict(row)))
        for row in raw_rows
    ]


def _records(binding: SessionOwnership) -> list[dict[str, Any]]:
    return [dict(record) for record in (binding.reservations or [])]


def _expires_at(record: dict[str, Any]) -> float | None:
    try:
        return float(record['expires_at'])
    except (KeyError, TypeError, ValueError):
        return None


def _reconcile_pending(
    records: list[dict[str, Any]], now: float
) -> tuple[list[dict[str, Any]], int]:
    retained = []
    removed = 0
    for record in records:
        expires_at = _expires_at(record)
        if (
            record.get('status') == ReservationStatus.PENDING.value
            and expires_at is not None
            and expires_at <= now
        ):
            removed += 1
        else:
            retained.append(record)
    return retained, removed


def _live_lease(record: dict[str, Any], now: float) -> bool:
    status = record.get('status')
    if status == ReservationStatus.ADOPTING.value:
        # Once filesystem adoption starts, time alone cannot prove whether the
        # durable file/metadata exists. Exact commit/rollback reconciliation is
        # required before a destructive session lifecycle operation.
        return True
    if status != ReservationStatus.PENDING.value:
        return False
    expires_at = _expires_at(record)
    # Malformed lease metadata fails closed rather than enabling destructive
    # lifecycle operations.
    return expires_at is None or expires_at > now


async def _cas(binding: SessionOwnership, updates: dict[str, Any]) -> SessionOwnership:
    if binding.id is None:
        raise OwnershipConflict('Ownership binding has no persistent id')
    conditions = {
        'id': str(binding.id),
        'session_id': binding.session_id,
        'user_id': binding.user_id,
        'agent_id': binding.agent_id,
        'state': binding.state.value,
        'generation': binding.generation,
        'version': binding.version,
    }
    values = dict(updates)
    values['version'] = binding.version + 1
    changed = await _settle_mutation(
        SessionOwnership.update_one(conditions, values)
    )
    if changed != 1:
        raise OwnershipConflict('Session ownership changed concurrently')
    refreshed = await SessionOwnership.get(str(binding.id))
    if refreshed is None:
        raise OwnershipConflict('Session ownership disappeared concurrently')
    return refreshed


async def reserve_intent(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    generation: int,
    promotion_token: str,
    size_bytes: int,
    now: float | None = None,
    ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS,
) -> SessionOwnership:
    """Atomically reserve one prospective document against session quota."""
    promotion_token = _identifier(promotion_token, 'promotion_token')
    size_bytes = int(size_bytes)
    ttl_seconds = float(ttl_seconds)
    if size_bytes <= 0:
        raise ValueError('size_bytes must be positive')
    if ttl_seconds <= 0:
        raise ValueError('ttl_seconds must be positive')
    now = time.time() if now is None else float(now)
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        if binding.state != OwnershipState.ACTIVE or binding.generation != int(generation):
            raise OwnershipConflict('Reservation generation is stale')
        records, _ = _reconcile_pending(_records(binding), now)
        existing = next(
            (record for record in records if record.get('promotion_token') == promotion_token),
            None,
        )
        if existing is not None:
            if (
                int(existing.get('generation', -1)) == binding.generation
                and int(existing.get('size_bytes') or 0) == size_bytes
                and existing.get('status') in {
                    ReservationStatus.PENDING.value,
                    ReservationStatus.ADOPTING.value,
                    ReservationStatus.COMMITTED.value,
                }
            ):
                return binding
            raise OwnershipConflict('Promotion token is already reserved')
        uncommitted = [
            record for record in records
            if record.get('status') in {
                ReservationStatus.PENDING.value,
                ReservationStatus.ADOPTING.value,
            }
        ]
        reserved_count = len(uncommitted)
        reserved_bytes = sum(int(record.get('size_bytes') or 0) for record in uncommitted)
        if binding.document_count + reserved_count + 1 > MAX_SESSION_DOCUMENTS:
            raise OwnershipQuotaExceeded('Session document count quota exceeded')
        if binding.document_bytes + reserved_bytes + size_bytes > MAX_SESSION_BYTES:
            raise OwnershipQuotaExceeded('Session document byte quota exceeded')
        records.append({
            'promotion_token': promotion_token,
            'size_bytes': size_bytes,
            'generation': binding.generation,
            'status': ReservationStatus.PENDING.value,
            'expires_at': now + ttl_seconds,
        })
        return await _cas(binding, {'reservations': records})


async def adopt_reservation(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    generation: int,
    promotion_token: str,
    now: float | None = None,
) -> SessionOwnership:
    """Move an exact live pending lease into filesystem/metadata adoption."""
    promotion_token = _identifier(promotion_token, 'promotion_token')
    now = time.time() if now is None else float(now)
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        if binding.state != OwnershipState.ACTIVE or binding.generation != int(generation):
            raise OwnershipConflict('Reservation generation is stale')
        records = _records(binding)
        target = next(
            (record for record in records if record.get('promotion_token') == promotion_token),
            None,
        )
        if target is None:
            raise ReservationNotFound('Reservation not found')
        if target.get('status') in {
            ReservationStatus.ADOPTING.value,
            ReservationStatus.COMMITTED.value,
        }:
            return binding
        if target.get('status') != ReservationStatus.PENDING.value or not _live_lease(target, now):
            raise OwnershipConflict('Reservation is not a live pending lease')
        target['status'] = ReservationStatus.ADOPTING.value
        return await _cas(binding, {'reservations': records})


async def commit_reservation(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    generation: int,
    promotion_token: str,
) -> SessionOwnership:
    """Convert an adopting lease into durable committed quota usage."""
    promotion_token = _identifier(promotion_token, 'promotion_token')
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        if binding.state != OwnershipState.ACTIVE or binding.generation != int(generation):
            raise OwnershipConflict('Reservation generation is stale')
        records = _records(binding)
        target = next(
            (record for record in records if record.get('promotion_token') == promotion_token),
            None,
        )
        if target is None:
            raise ReservationNotFound('Reservation not found')
        if target.get('status') == ReservationStatus.COMMITTED.value:
            return binding
        if target.get('status') != ReservationStatus.ADOPTING.value:
            raise OwnershipConflict('Reservation has not entered adoption')
        size_bytes = int(target.get('size_bytes') or 0)
        target['status'] = ReservationStatus.COMMITTED.value
        target.pop('expires_at', None)
        return await _cas(binding, {
            'reservations': records,
            'document_count': binding.document_count + 1,
            'document_bytes': binding.document_bytes + size_bytes,
        })


async def release_reservation(
    session_id: str,
    user_id: str,
    agent_id: str,
    promotion_token: str,
) -> SessionOwnership:
    """Release the exact token, including during rollback compensation."""
    promotion_token = _identifier(promotion_token, 'promotion_token')
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        records = _records(binding)
        target = next(
            (record for record in records if record.get('promotion_token') == promotion_token),
            None,
        )
        if target is None:
            return binding
        if target.get('status') == ReservationStatus.COMMITTED.value:
            raise OwnershipConflict('Committed quota requires document reconciliation')
        retained = [record for record in records if record is not target]
        return await _cas(binding, {'reservations': retained})


async def release_document(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    promotion_token: str,
    size_bytes: int,
) -> SessionOwnership:
    """Release committed quota only after exact document deletion succeeds."""
    promotion_token = _identifier(promotion_token, 'promotion_token')
    size_bytes = int(size_bytes)
    if size_bytes < 0:
        raise ValueError('size_bytes must be non-negative')
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        records = _records(binding)
        target = next(
            (record for record in records if record.get('promotion_token') == promotion_token),
            None,
        )
        if target is None:
            # Cleanup retries are idempotent. Absence proves this exact token is
            # no longer counted; never decrement an unrelated document.
            return binding
        if target.get('status') != ReservationStatus.COMMITTED.value:
            raise OwnershipConflict('Document reservation is not committed')
        if int(target.get('size_bytes') or 0) != size_bytes:
            raise OwnershipConflict('Committed document size does not match')
        if binding.document_count < 1 or binding.document_bytes < size_bytes:
            raise OwnershipConflict('Committed document quota would underflow')
        records.remove(target)
        return await _cas(binding, {
            'reservations': records,
            'document_count': binding.document_count - 1,
            'document_bytes': binding.document_bytes - size_bytes,
        })


async def reconcile_expired_reservations(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    now: float | None = None,
) -> int:
    """Remove only expired *pending* leases; adopting leases need exact action."""
    now = time.time() if now is None else float(now)
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        retained, removed = _reconcile_pending(_records(binding), now)
        if removed:
            await _cas(binding, {'reservations': retained})
        return removed


async def _begin_lifecycle(
    session_id: str,
    user_id: str,
    agent_id: str,
    state: OwnershipState,
    *,
    now: float | None = None,
) -> LifecycleToken:
    now = time.time() if now is None else float(now)
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        if binding.state != OwnershipState.ACTIVE:
            raise OwnershipConflict('Session lifecycle transition is already in progress')
        records, _ = _reconcile_pending(_records(binding), now)
        if any(_live_lease(record, now) for record in records):
            raise LifecycleLeaseActive('Session has an active document promotion lease')
        updated = await _cas(binding, {
            'state': state.value,
            'generation': binding.generation + 1,
            'reservations': records,
        })
        return LifecycleToken(
            binding_id=str(updated.id),
            session_id=updated.session_id,
            user_id=updated.user_id,
            agent_id=updated.agent_id,
            state=updated.state,
            generation=updated.generation,
            version=updated.version,
        )


async def begin_clear(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    now: float | None = None,
) -> LifecycleToken:
    return await _begin_lifecycle(
        session_id, user_id, agent_id, OwnershipState.CLEARING, now=now
    )


async def begin_delete(
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    now: float | None = None,
) -> LifecycleToken:
    return await _begin_lifecycle(
        session_id, user_id, agent_id, OwnershipState.DELETING, now=now
    )


async def resume_lifecycle(
    session_id: str,
    user_id: str,
    agent_id: str,
    state: OwnershipState | str,
) -> LifecycleToken:
    """Return the exact persisted lifecycle token for idempotent recovery."""
    try:
        expected_state = OwnershipState(state)
    except ValueError as exc:
        raise OwnershipConflict('Invalid lifecycle recovery state') from exc
    if expected_state == OwnershipState.ACTIVE:
        raise OwnershipConflict('Active sessions have no lifecycle to resume')
    async with _lock_for(_mutation_locks, session_id):
        binding = await require_owned(session_id, user_id, agent_id)
        if binding.state != expected_state:
            raise OwnershipConflict('Session is in a different lifecycle state')
        return LifecycleToken(
            binding_id=str(binding.id),
            session_id=binding.session_id,
            user_id=binding.user_id,
            agent_id=binding.agent_id,
            state=binding.state,
            generation=binding.generation,
            version=binding.version,
        )


async def _load_lifecycle_token(token: LifecycleToken) -> SessionOwnership:
    binding = await SessionOwnership.get(token.binding_id)
    if (
        binding is None
        or binding.session_id != token.session_id
        or binding.user_id != token.user_id
        or binding.agent_id != token.agent_id
        or binding.state != token.state
        or binding.generation != token.generation
        or binding.version != token.version
    ):
        raise OwnershipConflict('Session lifecycle token is stale')
    return binding


async def finish_clear(token: LifecycleToken) -> SessionOwnership:
    if token.state != OwnershipState.CLEARING:
        raise OwnershipConflict('Not a clear lifecycle token')
    async with _lock_for(_mutation_locks, token.session_id):
        binding = await _load_lifecycle_token(token)
        return await _cas(binding, {
            'state': OwnershipState.ACTIVE.value,
            'document_count': 0,
            'document_bytes': 0,
            'reservations': [],
        })


async def abort_lifecycle(token: LifecycleToken) -> SessionOwnership:
    async with _lock_for(_mutation_locks, token.session_id):
        binding = await _load_lifecycle_token(token)
        return await _cas(binding, {'state': OwnershipState.ACTIVE.value})


async def finish_delete(token: LifecycleToken) -> None:
    """Delete the exact deleting binding; callers invoke this last."""
    if token.state != OwnershipState.DELETING:
        raise OwnershipConflict('Not a delete lifecycle token')
    async with _lock_for(_mutation_locks, token.session_id):
        binding = await _load_lifecycle_token(token)
        deleted = await _settle_mutation(SessionOwnership.delete_many({
            'id': str(binding.id),
            'session_id': binding.session_id,
            'user_id': binding.user_id,
            'agent_id': binding.agent_id,
            'state': binding.state.value,
            'generation': binding.generation,
            'version': binding.version,
        }))
        if deleted != 1:
            raise OwnershipConflict('Session ownership changed concurrently')


class SessionOwnershipService:
    """Stable injectable facade used by document/session transports."""

    claim_new = staticmethod(claim_new)
    discard_fresh_claim = staticmethod(discard_fresh_claim)
    require_owned = staticmethod(require_owned)
    require_active_owned = staticmethod(require_active_owned)
    owned_session_ids = staticmethod(owned_session_ids)
    reconciliation_bindings = staticmethod(reconciliation_bindings)
    reserve_intent = staticmethod(reserve_intent)
    adopt_reservation = staticmethod(adopt_reservation)
    commit_reservation = staticmethod(commit_reservation)
    release_reservation = staticmethod(release_reservation)
    release_document = staticmethod(release_document)
    reconcile_expired_reservations = staticmethod(reconcile_expired_reservations)
    begin_clear = staticmethod(begin_clear)
    begin_delete = staticmethod(begin_delete)
    resume_lifecycle = staticmethod(resume_lifecycle)
    finish_clear = staticmethod(finish_clear)
    finish_delete = staticmethod(finish_delete)
    abort_lifecycle = staticmethod(abort_lifecycle)


session_ownerships = SessionOwnershipService()
