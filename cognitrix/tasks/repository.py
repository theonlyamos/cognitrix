"""Durable compare-and-set repository for task-run lifecycle state.

The run row is the serialization point for claims, worker mutations, and event
sequence allocation. Event delivery uses an outbox: a state CAS first appends a
complete event envelope, then ``flush_outbox`` inserts it idempotently and
acknowledges the head with another CAS.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from pydantic import BaseModel

from cognitrix.errors import ExecutionControlError
from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.metrics import TaskRunPhaseMetric
from cognitrix.tasks.results import StepResult
from cognitrix.tasks.runtime import AgentRuntimeSnapshot
from cognitrix.tasks.run import (
    RUN_TIMESTAMP_FORMAT,
    TaskRun,
    TaskRunHead,
    TaskRunStatus,
    run_acl_snapshot,
)
from cognitrix.tasks.step import TaskRunStep, TaskRunStepStatus

logger = logging.getLogger("cognitrix.log")

MAX_CAS_ATTEMPTS = 64
DEFAULT_CANCEL_GRACE_SECONDS = 10.0
DEFAULT_HEAD_RESERVATION_TIMEOUT_SECONDS = 300.0
DEFAULT_HEAD_RECONCILIATION_BATCH_SIZE = 100
DEFAULT_NOTIFICATION_LEASE_SECONDS = 60
MAX_NOTIFICATION_ATTEMPTS = 8
NOTIFICATION_BACKOFF_SECONDS = (30, 120, 600, 1800, 3600, 7200, 21600)
_ACTIVE_STATUSES = {
    TaskRunStatus.QUEUED,
    TaskRunStatus.RUNNING,
    TaskRunStatus.CANCELLING,
}
_LEASED_STATUSES = {TaskRunStatus.RUNNING, TaskRunStatus.CANCELLING}
_TERMINAL_STATUSES = {
    TaskRunStatus.COMPLETED,
    TaskRunStatus.FAILED,
    TaskRunStatus.CANCELLED,
}
_RUN_MUTABLE_FIELDS = {
    "status",
    "plan",
    "result",
    "result_data",
    "usage",
    "error_code",
    "error",
    "started_at",
    "completed_at",
}
_STEP_MUTABLE_FIELDS = {
    "status",
    "attempts",
    "result",
    "gate",
    "error",
    "started_at",
    "completed_at",
}
_STEP_DEFINITION_FIELDS = (
    "run_id",
    "task_id",
    "step_index",
    "title",
    "description",
    "expected_output",
    "verification_criteria",
    "agent_name",
    "dependencies",
    "required_tools",
    "runtime_snapshot",
)
_STEP_STATUS_TRANSITIONS = {
    TaskRunStepStatus.PENDING.value: {
        TaskRunStepStatus.RUNNING.value,
        TaskRunStepStatus.SKIPPED.value,
        TaskRunStepStatus.CANCELLED.value,
    },
    TaskRunStepStatus.RUNNING.value: {
        TaskRunStepStatus.DONE.value,
        TaskRunStepStatus.FAILED.value,
        TaskRunStepStatus.CANCELLED.value,
    },
    TaskRunStepStatus.DONE.value: set(),
    TaskRunStepStatus.FAILED.value: set(),
    TaskRunStepStatus.SKIPPED.value: set(),
    TaskRunStepStatus.CANCELLED.value: set(),
}
_USAGE_COUNTER_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "llm_calls",
    "tool_calls",
    "tool_attempts",
    "retries",
    "steps",
)
_USAGE_RESERVATION_COUNTER_FIELDS = ("reserved_tokens",)
_USAGE_DECIMAL_FIELDS = ("cost_usd", "reserved_cost_usd")
_USAGE_FIELDS = {
    *_USAGE_COUNTER_FIELDS,
    *_USAGE_RESERVATION_COUNTER_FIELDS,
    *_USAGE_DECIMAL_FIELDS,
}
_NOTIFICATION_PENDING = "pending"
_NOTIFICATION_DELIVERING = "delivering"
_NOTIFICATION_DELIVERED = "delivered"
_NOTIFICATION_SKIPPED = "skipped"
_NOTIFICATION_FAILED = "failed"


class ActiveRunExists(RuntimeError):
    """A task already owns a queued/running/cancelling run."""


class TaskDeleted(RuntimeError):
    """A task's durable admission head has been tombstoned."""


class LeaseLost(ExecutionControlError):
    """The caller no longer owns the run's current lease generation."""


class RunStateConflict(RuntimeError):
    """The run did not match the requested state transition."""


class TaskRunHeadInvariantError(RuntimeError):
    """Legacy task-run history cannot be represented by one active head."""


class UnsupportedDurableTaskBackend(RuntimeError):
    """The configured datastore cannot provide durable task fencing."""


def _require_supported_durable_backend() -> None:
    from odbms import DBMS

    dbms = getattr(DBMS.Database, "dbms", "")
    if dbms == "mongodb":
        raise UnsupportedDurableTaskBackend(
            "Durable task execution requires SQLite, PostgreSQL, or MySQL; "
            "MongoDB cannot atomically fence run and step rows"
        )


@dataclass(frozen=True)
class LeaseClaim:
    run_id: str
    owner: str
    generation: int


@dataclass
class _LoopLocks:
    guard: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: dict[str, asyncio.Lock] = field(default_factory=dict)


@dataclass
class _DatabaseIndexSetup:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    complete: bool = False


_LOCKS_BY_LOOP: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_INDEX_SETUP_BY_DATABASE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class _TaskRunHistory:
    latest_run_id: str | None
    active_run_id: str | None


def _loop_locks() -> _LoopLocks:
    loop = asyncio.get_running_loop()
    locks = _LOCKS_BY_LOOP.get(loop)
    if locks is None:
        locks = _LoopLocks()
        _LOCKS_BY_LOOP[loop] = locks
    return locks


async def _task_creation_lock(task_id: str) -> asyncio.Lock:
    locks = _loop_locks()
    async with locks.guard:
        return locks.tasks.setdefault(task_id, asyncio.Lock())


def _database_index_setup(database: Any) -> _DatabaseIndexSetup:
    setup = _INDEX_SETUP_BY_DATABASE.get(database)
    if setup is None:
        setup = _DatabaseIndexSetup()
        _INDEX_SETUP_BY_DATABASE[database] = setup
    return setup


def _timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).replace(tzinfo=None).strftime(
        RUN_TIMESTAMP_FORMAT
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, RUN_TIMESTAMP_FORMAT)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _force_cancel_ready(
    run: TaskRun,
    *,
    now: datetime,
    grace_seconds: float,
) -> bool:
    lease_expires_at = _parse_timestamp(run.lease_expires_at)
    if lease_expires_at is None or lease_expires_at <= now:
        return True
    cancel_requested_at = _parse_timestamp(run.cancel_requested_at)
    return bool(
        cancel_requested_at is not None
        and now >= cancel_requested_at + timedelta(seconds=grace_seconds)
    )


def _terminal_notification_state(run: TaskRun) -> str:
    return (
        _NOTIFICATION_PENDING
        if run.callback_url and run.callback_key_id
        else _NOTIFICATION_SKIPPED
    )


def _lease_is_live(run: TaskRun, *, now: datetime | None = None) -> bool:
    expires = _parse_timestamp(run.lease_expires_at)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return expires is not None and expires > current


def _uses_database_lease_clock() -> bool:
    """Whether write predicates can authoritatively compare DB server time."""
    from odbms import DBMS

    return getattr(DBMS.Database, "dbms", "") in {
        "sqlite",
        "postgresql",
        "mysql",
    }


async def _database_claim_is_live(run: TaskRun, claim: LeaseClaim) -> bool:
    """Classify a failed write with the same server-clock lease predicate."""
    from odbms import DBMS

    database = DBMS.Database
    dbms = getattr(database, "dbms", "")
    if dbms not in ("sqlite", "postgresql", "mysql"):
        return _lease_is_live(run)
    marker = (
        (lambda name: f":{name}")
        if dbms == "sqlite"
        else (lambda name: f"%({name})s")
    )
    records = await _relational_records(
        database,
        f"SELECT id FROM {TaskRun.table_name()} "
        f"WHERE id = {marker('run_id')} "
        f"AND status = {marker('run_status')} "
        f"AND lease_owner = {marker('lease_owner')} "
        f"AND lease_generation = {marker('lease_generation')} "
        f"AND {_lease_expiry_predicate(dbms, 'lease_expires_at')} "
        "LIMIT 1",
        {
            "run_id": run.id,
            "run_status": run.status.value,
            "lease_owner": claim.owner,
            "lease_generation": claim.generation,
        },
    )
    return bool(records)


def _lease_expiry_predicate(dbms: str, column: str) -> str:
    """Use the database clock at the same statement that performs the write."""
    if dbms == "sqlite":
        return (
            f"{column} > STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW')"
        )
    if dbms == "postgresql":
        return (
            f"{column} > TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD HH24:MI:SS')"
        )
    if dbms == "mysql":
        return f"{column} > CAST(UTC_TIMESTAMP() AS CHAR)"
    raise RunStateConflict(
        f"Atomic lease expiry fencing requires a relational database, got {dbms!r}"
    )


def _database_time_expression(dbms: str) -> str:
    """Return UTC database time formatted like every persisted run timestamp."""
    if dbms == "sqlite":
        return "STRFTIME('%Y-%m-%d %H:%M:%S', 'NOW')"
    if dbms == "postgresql":
        return (
            "TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD HH24:MI:SS')"
        )
    if dbms == "mysql":
        return "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')"
    raise RunStateConflict(
        "Database-clock task timestamps require a relational database, "
        f"got {dbms!r}"
    )


def _database_lease_expiry_expression(dbms: str, lease_marker: str) -> str:
    """Return DB now plus a bound lease duration in run timestamp format."""
    if dbms == "sqlite":
        return (
            "STRFTIME('%Y-%m-%d %H:%M:%S', 'NOW', "
            f"'+' || {lease_marker} || ' seconds')"
        )
    if dbms == "postgresql":
        return (
            "TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + "
            f"({lease_marker} * INTERVAL '1 second'), "
            "'YYYY-MM-DD HH24:MI:SS')"
        )
    if dbms == "mysql":
        return (
            "DATE_FORMAT(TIMESTAMPADD(SECOND, "
            f"{lease_marker}, UTC_TIMESTAMP()), '%Y-%m-%d %H:%i:%s')"
        )
    raise RunStateConflict(
        f"Database-clock task leases require a relational database, got {dbms!r}"
    )


def _database_updated_at_expression(dbms: str) -> str:
    """Return a native timestamp expression for the inherited audit column."""
    if dbms == "sqlite":
        return "CURRENT_TIMESTAMP"
    if dbms == "postgresql":
        return "CURRENT_TIMESTAMP AT TIME ZONE 'UTC'"
    if dbms == "mysql":
        return "UTC_TIMESTAMP()"
    raise RunStateConflict(
        "Database-clock audit timestamps require a relational database, "
        f"got {dbms!r}"
    )


def _recovery_stale_predicate(
    dbms: str,
    *,
    status: TaskRunStatus,
    marker,
) -> str:
    """Fence recovery against the database clock in its terminal UPDATE."""
    if status in _LEASED_STATUSES:
        if dbms == "sqlite":
            now = "STRFTIME('%Y-%m-%d %H:%M:%S', 'NOW')"
        elif dbms == "postgresql":
            now = (
                "TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', "
                "'YYYY-MM-DD HH24:MI:SS')"
            )
        elif dbms == "mysql":
            now = "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')"
        else:
            raise RunStateConflict(
                "Atomic recovery lease fencing requires a relational database"
            )
        return f"(lease_expires_at IS NULL OR lease_expires_at <= {now})"

    if status != TaskRunStatus.QUEUED:
        raise RunStateConflict(
            f"Task run recovery is unsupported from status {status.value}"
        )
    timeout = marker("queue_timeout_seconds")
    if dbms == "sqlite":
        cutoff = (
            "STRFTIME('%Y-%m-%d %H:%M:%S', 'NOW', "
            f"'-' || {timeout} || ' seconds')"
        )
    elif dbms == "postgresql":
        cutoff = (
            "TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - "
            f"({timeout} * INTERVAL '1 second'), "
            "'YYYY-MM-DD HH24:MI:SS')"
        )
    elif dbms == "mysql":
        cutoff = (
            "DATE_FORMAT(TIMESTAMPADD(SECOND, "
            f"-{timeout}, UTC_TIMESTAMP()), '%Y-%m-%d %H:%i:%s')"
        )
    else:
        raise RunStateConflict(
            "Atomic recovery queue fencing requires a relational database"
        )
    return f"(queued_at IS NULL OR queued_at <= {cutoff})"


def _status_value(value: TaskRunStatus | str) -> str:
    return value.value if isinstance(value, TaskRunStatus) else str(value).lower()


def _status_set(values: Iterable[TaskRunStatus | str] | None) -> set[str] | None:
    if values is None:
        return None
    return {_status_value(value) for value in values}


def _step_status_value(value: TaskRunStepStatus | str) -> str:
    if isinstance(value, TaskRunStepStatus):
        return value.value
    return str(value).lower()


def _step_status_set(
    values: Iterable[TaskRunStepStatus | str] | None,
) -> set[str] | None:
    if values is None:
        return None
    return {_step_status_value(value) for value in values}


def _step_from_plan_entry(
    run: TaskRun,
    entry: dict[str, Any],
    position: int,
) -> TaskRunStep:
    index = int(entry.get("index", entry.get("step_index", position)))
    return TaskRunStep(
        run_id=str(run.id),
        task_id=run.task_id,
        step_index=index,
        title=str(entry.get("title") or f"Step {index + 1}"),
        description=str(entry.get("description") or ""),
        expected_output=str(entry.get("expected_output") or ""),
        verification_criteria=str(entry.get("verification_criteria") or ""),
        agent_name=str(entry.get("agent_name") or ""),
        dependencies=list(entry.get("dependencies") or []),
        required_tools=entry.get("required_tools"),
        runtime_snapshot=entry.get("runtime_snapshot"),
        status=entry.get("status") or TaskRunStepStatus.PENDING,
        attempts=int(entry.get("attempts") or 0),
        result=entry.get("result"),
        gate=entry.get("gate"),
        error=entry.get("error"),
        started_at=entry.get("started_at"),
        completed_at=entry.get("completed_at"),
    )


def _step_update_patch(updates: dict[str, Any]) -> dict[str, Any]:
    unknown = set(updates) - set(TaskRunStep.model_fields)
    immutable = set(updates) - _STEP_MUTABLE_FIELDS - unknown
    if unknown:
        raise ValueError(f"Unknown task-run step fields: {sorted(unknown)}")
    if immutable:
        raise ValueError(f"Immutable task-run step fields: {sorted(immutable)}")

    patch: dict[str, Any] = {}
    for key, value in updates.items():
        if key == "status":
            patch[key] = _step_status_value(value)
        elif key == "result" and value is not None:
            patch[key] = StepResult.from_stored(value).model_dump(mode="json")
        elif isinstance(value, BaseModel):
            patch[key] = value.model_dump(mode="json")
        else:
            patch[key] = value
    return patch


def _run_update_patch(updates: dict[str, Any]) -> dict[str, Any]:
    unknown = set(updates) - set(TaskRun.model_fields)
    immutable = set(updates) - _RUN_MUTABLE_FIELDS - unknown
    if unknown:
        raise ValueError(f"Unknown task-run fields: {sorted(unknown)}")
    if immutable:
        raise ValueError(f"Immutable task-run fields: {sorted(immutable)}")

    patch = dict(updates)
    if "status" in patch:
        patch["status"] = _status_value(patch["status"])
    return patch


def _assert_same_step_definition(
    stored: TaskRunStep,
    candidate: TaskRunStep,
) -> None:
    stored_data = stored.model_dump(mode="json")
    candidate_data = candidate.model_dump(mode="json")
    mismatched = [
        field
        for field in _STEP_DEFINITION_FIELDS
        if stored_data.get(field) != candidate_data.get(field)
    ]
    if mismatched:
        raise RunStateConflict(
            f"Task run {candidate.run_id} step {candidate.step_index} definition "
            f"does not match existing row: {mismatched}"
        )


def _require_legal_step_transition(current: str, target: str) -> None:
    if target == current:
        return
    if target not in _STEP_STATUS_TRANSITIONS[current]:
        raise RunStateConflict(
            f"Illegal task-run step transition: {current} -> {target}"
        )


def _normalise_usage_snapshot(snapshot: dict[str, Any]) -> dict[str, int | str]:
    unknown = set(snapshot) - _USAGE_FIELDS
    if unknown:
        raise ValueError(f"Unknown task usage fields: {sorted(unknown)}")

    usage: dict[str, int | str] = {}
    for field in (*_USAGE_COUNTER_FIELDS, *_USAGE_RESERVATION_COUNTER_FIELDS):
        if field not in snapshot:
            continue
        value = snapshot[field]
        if isinstance(value, bool):
            raise ValueError(f"Task usage {field} must be a non-negative integer")
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"Task usage {field} must be a non-negative integer"
            ) from exc
        if (
            not decimal_value.is_finite()
            or decimal_value < 0
            or decimal_value != decimal_value.to_integral_value()
        ):
            raise ValueError(f"Task usage {field} must be a non-negative integer")
        usage[field] = int(decimal_value)

    for field in _USAGE_DECIMAL_FIELDS:
        if field not in snapshot:
            continue
        try:
            cost = Decimal(str(snapshot[field]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"Task usage {field} must be a non-negative decimal"
            ) from exc
        if not cost.is_finite() or cost < 0:
            raise ValueError(
                f"Task usage {field} must be a non-negative decimal"
            )
        usage[field] = format(cost, "f")
    return usage


def _merge_usage(
    stored: dict[str, Any],
    snapshot: dict[str, int | str],
) -> dict[str, Any]:
    """Merge cumulative counters and replace the current reservation gauges."""
    merged = dict(stored)
    for field in _USAGE_COUNTER_FIELDS:
        if field not in snapshot:
            continue
        try:
            current_value = Decimal(str(stored.get(field, 0) or 0))
            if (
                not current_value.is_finite()
                or current_value != current_value.to_integral_value()
            ):
                raise ValueError
            current = int(current_value)
        except (InvalidOperation, OverflowError, TypeError, ValueError) as exc:
            raise RunStateConflict(
                f"Stored task usage {field} is not an integer"
            ) from exc
        merged[field] = max(0, current, int(snapshot[field]))

    if "cost_usd" in snapshot:
        try:
            current_cost = Decimal(str(stored.get("cost_usd", "0") or "0"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RunStateConflict(
                "Stored task usage cost_usd is not a decimal"
            ) from exc
        if not current_cost.is_finite() or current_cost < 0:
            raise RunStateConflict(
                "Stored task usage cost_usd is not a non-negative decimal"
            )
        snapshot_cost = Decimal(str(snapshot["cost_usd"]))
        merged["cost_usd"] = format(max(current_cost, snapshot_cost), "f")

    # Reservations describe work currently in flight, so unlike cumulative
    # usage they must be allowed to fall back to zero after reconciliation.
    for field in (*_USAGE_RESERVATION_COUNTER_FIELDS, "reserved_cost_usd"):
        if field in snapshot:
            merged[field] = snapshot[field]
    return merged


def _event_envelope(
    run_id: str,
    sequence: int,
    event: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "session_id": event.get("session_id"),
        "step_index": event.get("step_index"),
        "sequence": sequence,
        "kind": str(event["kind"]),
        "agent_name": event.get("agent_name"),
        "data": dict(event.get("data") or {}),
    }


async def _insert_with_explicit_id(model, instance) -> None:
    """Insert a model whose primary key is assigned before persistence.

    odbms's SQLite adapter unconditionally drops string ids. Bypass only that
    adapter's insert helper; other backends retain their normal parameter and
    transaction handling.
    """
    from odbms import DBMS

    data = instance.model_dump()
    if getattr(DBMS.Database, "dbms", "") != "sqlite":
        await model.insert(data)
        return
    params = model.normalise(data, "params")
    columns = ", ".join(params)
    placeholders = ", ".join(f":{name}" for name in params)
    await DBMS.Database.query(
        f"INSERT INTO {model.table_name()} ({columns}) VALUES ({placeholders})",
        params,
    )


async def _cursor_records(cursor) -> list[dict[str, Any]]:
    if cursor is None:
        return []
    description = getattr(cursor, "description", None)
    rows = cursor.fetchall() if hasattr(cursor, "fetchall") else cursor
    if inspect.isawaitable(rows):
        rows = await rows
    columns = [item[0] for item in (description or [])]
    records: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, Mapping):
            records.append(dict(row))
            continue
        try:
            records.append(dict(row))
        except (TypeError, ValueError):
            records.append(dict(zip(columns, row)))
    return records


async def _relational_records(database, statement, params=None) -> list[dict]:
    """Fetch rows before an ODBMS pooled cursor leaves its connection."""
    pool = getattr(database, "_pool", None)
    if pool is None:
        return await _cursor_records(await database.query(statement, params))
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(statement, params or {})
            return await _cursor_records(cursor)


async def _database_timestamp() -> str:
    """Read UTC time from the configured relational server.

    Queue creation still uses the backend adapter's established insert path;
    only the timestamp value comes from this read. Unknown lightweight fakes
    retain the process-clock fallback, while supported production backends
    never depend on worker clock skew.
    """
    from odbms import DBMS

    database = DBMS.Database
    dbms = getattr(database, "dbms", "")
    if dbms not in ("sqlite", "postgresql", "mysql"):
        return _timestamp()
    records = await _relational_records(
        database,
        f"SELECT {_database_time_expression(dbms)} AS current_time",
    )
    if not records or records[0].get("current_time") is None:
        raise RunStateConflict("Could not read the database clock")
    value = records[0]["current_time"]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return _timestamp(value)
    parsed = _parse_timestamp(str(value))
    if parsed is None:
        raise RunStateConflict("Database returned an invalid UTC timestamp")
    return _timestamp(parsed)


async def _recovery_model_query(
    model,
    *,
    relational_where: str,
    relational_params: dict[str, Any] | None = None,
    mongo_conditions: dict[str, Any],
) -> list:
    """Run adapter-native recovery predicates ODBMS Model.find cannot express."""
    from odbms import DBMS

    database = DBMS.Database
    dbms = getattr(database, "dbms", "")
    if dbms == "mongodb":
        rows = await database.find(
            model.table_name(),
            mongo_conditions,
            limit=0,
            sort=[("id", 1)],
        )
    elif dbms in ("sqlite", "postgresql", "mysql"):
        rows = await _relational_records(
            database,
            f"SELECT * FROM {model.table_name()} "
            f"WHERE {relational_where} ORDER BY id",
            relational_params or {},
        )
    else:
        raise RuntimeError(
            f"Indexed task recovery is unsupported for database {dbms!r}"
        )
    return [model(**model.normalise(row)) for row in rows]


class RunRepository:
    """CAS-backed persistence for durable task runs."""

    def force_cancel_ready(
        self,
        run: TaskRun,
        *,
        now: datetime | None = None,
        grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
    ) -> bool:
        """Return the server-authoritative force-cancel eligibility."""
        if grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")
        if run.status != TaskRunStatus.CANCELLING:
            return False
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        else:
            current_time = current_time.astimezone(timezone.utc)
        return _force_cancel_ready(
            run,
            now=current_time,
            grace_seconds=grace_seconds,
        )

    async def _ensure_indexes(self) -> None:
        """Install repository invariants for lightweight test/local schemas.

        Normal application startup creates these indexes in ``config.py``.
        Repository-local creation keeps direct/test callers safe too. The
        partial active-run index is available on SQLite/PostgreSQL; the
        process-local creation lock remains the fallback on other adapters.
        """
        from odbms import DBMS

        database = DBMS.Database
        setup = _database_index_setup(database)
        if setup.complete:
            return
        async with setup.lock:
            if setup.complete:
                return
            dbms = getattr(database, "dbms", "")
            if dbms == "sqlite":
                # odbms.update_one already writes updated_at. Its generated AFTER
                # UPDATE trigger performs a second physical UPDATE, which is both
                # redundant and observable to audit/changefeed triggers. Step
                # transitions must be exactly one target-row write.
                try:
                    await database.query(
                        f"DROP TRIGGER IF EXISTS update_{TaskRunStep.table_name()}"
                    )
                except Exception:
                    logger.debug(
                        "Could not remove redundant task-step timestamp trigger",
                        exc_info=True,
                    )
            statements = [
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_task_run_events_run_sequence "
                "ON taskrunevents (run_id, sequence)",
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_task_run_steps_run_step "
                "ON taskrunsteps (run_id, step_index)",
            ]
            if dbms in ("sqlite", "postgresql"):
                statements.append(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_task_runs_one_active "
                    "ON taskruns (task_id) "
                    "WHERE status IN ('queued', 'running', 'cancelling')"
                )
            for statement in statements:
                try:
                    await database.query(statement)
                except Exception:
                    # Full startup owns cross-database migration. Direct callers
                    # still retain CAS + process-local serialization if a backend
                    # does not support one of these optional statements.
                    logger.debug(
                        "Could not establish repository index", exc_info=True
                    )
            setup.complete = True

    async def _task_run_history(self, task_id: str) -> _TaskRunHistory:
        """Read only the newest row and at most two active legacy rows.

        Two active rows are sufficient to prove that a portable one-row head
        cannot safely represent the stored history.  The deterministic order
        makes the resulting startup error stable across reruns and adapters.
        """
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            raise TaskRunHeadInvariantError(
                f"Task-run head reconciliation is unsupported for {dbms!r}"
            )
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        task_marker = marker("task_id")
        order = (
            "CASE WHEN created_at IS NULL THEN 0 ELSE 1 END DESC, "
            "created_at DESC, id DESC"
        )
        active_params = {
            "task_id": task_id,
            "active_queued": TaskRunStatus.QUEUED.value,
            "active_running": TaskRunStatus.RUNNING.value,
            "active_cancelling": TaskRunStatus.CANCELLING.value,
        }
        active_rows = await _relational_records(
            database,
            "SELECT id FROM taskruns "
            f"WHERE task_id = {task_marker} AND status IN ("
            f"{marker('active_queued')}, {marker('active_running')}, "
            f"{marker('active_cancelling')}) "
            f"ORDER BY {order} LIMIT 2",
            active_params,
        )
        active_ids = [str(row["id"]) for row in active_rows]
        if len(active_ids) > 1:
            raise TaskRunHeadInvariantError(
                f"Task {task_id} has multiple active legacy runs: "
                + ", ".join(active_ids)
            )

        latest_rows = await _relational_records(
            database,
            "SELECT id FROM taskruns "
            f"WHERE task_id = {task_marker} ORDER BY {order} LIMIT 1",
            {"task_id": task_id},
        )
        return _TaskRunHistory(
            latest_run_id=(
                str(latest_rows[0]["id"])
                if latest_rows
                else None
            ),
            active_run_id=active_ids[0] if active_ids else None,
        )

    async def reconcile_task_head(self, task_id: str) -> TaskRunHead | None:
        """Seed or repair one head from bounded authoritative run history.

        Head writes use the same primary-key insert and version CAS as enqueue.
        A head whose active id has no run row is left intact: it can be a
        creator paused between reservation and insert, and stealing it would
        admit a second run.  Stale missing-row reservations are released only
        by the existing timeout-based recovery path.
        """
        history = await self._task_run_history(task_id)
        for _ in range(MAX_CAS_ATTEMPTS):
            head = await TaskRunHead.get(task_id)
            if head is None:
                if history.latest_run_id is None:
                    return None
                candidate = TaskRunHead(
                    task_id=task_id,
                    latest_run_id=history.latest_run_id,
                    active_run_id=history.active_run_id,
                    version=1,
                )
                candidate.id = task_id
                try:
                    await _insert_with_explicit_id(TaskRunHead, candidate)
                except Exception:
                    if await TaskRunHead.get(task_id) is None:
                        raise
                    await asyncio.sleep(0)
                    continue
                stored = await TaskRunHead.get(task_id)
                if stored is None:
                    raise RunStateConflict(
                        f"Task-run head disappeared after reconciliation for {task_id}"
                    )
                return stored

            if head.deleted_at:
                # Deletion is an admission decision, not a projection to
                # reconstruct from historical runs. Never reconcile it away.
                return head

            desired_latest = history.latest_run_id
            desired_active = history.active_run_id
            if head.active_run_id:
                reserved = await TaskRun.get(head.active_run_id)
                if reserved is None:
                    # A concurrent enqueue may own a pre-insert reservation.
                    return head
                if reserved.status in _ACTIVE_STATUSES:
                    if (
                        history.active_run_id is not None
                        and history.active_run_id != head.active_run_id
                    ):
                        conflicting = sorted(
                            {history.active_run_id, str(head.active_run_id)}
                        )
                        raise TaskRunHeadInvariantError(
                            f"Task {task_id} has conflicting active runs: "
                            + ", ".join(conflicting)
                        )
                    # A concurrent creator can insert after the bounded history
                    # reads.  Its reserved head remains the newer projection.
                    desired_active = str(head.active_run_id)
                    if history.active_run_id != head.active_run_id:
                        desired_latest = head.latest_run_id

            if (
                head.latest_run_id == desired_latest
                and head.active_run_id == desired_active
            ):
                return head
            changed = await TaskRunHead.update_one(
                {"id": task_id, "version": head.version},
                {
                    "latest_run_id": desired_latest,
                    "active_run_id": desired_active,
                    "version": head.version + 1,
                },
            )
            if changed == 1:
                stored = await TaskRunHead.get(task_id)
                if stored is None:
                    raise RunStateConflict(
                        f"Task-run head disappeared after reconciliation for {task_id}"
                    )
                return stored
            await asyncio.sleep(0)
        raise RunStateConflict(f"Could not reconcile task-run head for {task_id}")

    async def reconcile_heads(
        self,
        *,
        batch_size: int = DEFAULT_HEAD_RECONCILIATION_BATCH_SIZE,
    ) -> int:
        """Reconcile every historical task using bounded keyset pages."""
        _require_supported_durable_backend()
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
        ):
            raise ValueError("batch_size must be a positive integer")
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            raise TaskRunHeadInvariantError(
                f"Task-run head reconciliation is unsupported for {dbms!r}"
            )
        marker = ":after_task" if dbms == "sqlite" else "%(after_task)s"
        after_task = ""
        reconciled = 0
        while True:
            rows = await _relational_records(
                database,
                "SELECT DISTINCT task_id FROM taskruns "
                "WHERE task_id IS NOT NULL AND task_id <> '' "
                f"AND task_id > {marker} ORDER BY task_id LIMIT {int(batch_size)}",
                {"after_task": after_task},
            )
            if not rows:
                return reconciled
            task_ids = [str(row["task_id"]) for row in rows]
            for task_id in task_ids:
                await self.reconcile_task_head(task_id)
                reconciled += 1
            after_task = task_ids[-1]
            if len(task_ids) < batch_size:
                return reconciled

    async def tombstone_task(
        self,
        task_id: str,
        *,
        deleted_at: str,
    ) -> TaskRunHead:
        """Atomically close run admission while the head is inactive.

        This CAS shares the exact versioned row used by enqueue reservation.
        Whichever operation wins is therefore authoritative: deletion cannot
        slip between enqueue's Task-row check and broker publication, and a
        pre-insert reservation is conservatively treated as active.
        """
        _require_supported_durable_backend()
        if not task_id:
            raise ValueError("task_id is required")
        if not deleted_at:
            raise ValueError("deleted_at is required")

        # Upgrade-safe deletion: a pre-head active TaskRun is still active.
        # Seed the head from bounded history before competing on its version.
        await self.reconcile_task_head(task_id)

        for _ in range(MAX_CAS_ATTEMPTS):
            head = await TaskRunHead.get(task_id)
            if head is None:
                candidate = TaskRunHead(
                    task_id=task_id,
                    deleted_at=deleted_at,
                    version=1,
                )
                candidate.id = task_id
                try:
                    await _insert_with_explicit_id(TaskRunHead, candidate)
                except Exception:
                    if await TaskRunHead.get(task_id) is None:
                        raise
                    await asyncio.sleep(0)
                    continue
                stored = await TaskRunHead.get(task_id)
                if stored is None:
                    raise RunStateConflict(
                        f"Task-run head disappeared after deletion for {task_id}"
                    )
                return stored

            if head.deleted_at:
                return head
            if head.active_run_id:
                active = await TaskRun.get(head.active_run_id)
                if active is None or active.status in _ACTIVE_STATUSES:
                    raise ActiveRunExists(
                        f"Task {task_id} already has an active run"
                    )

            changed = await TaskRunHead.update_one(
                {"id": task_id, "version": head.version},
                {
                    "active_run_id": None,
                    "deleted_at": deleted_at,
                    "version": head.version + 1,
                },
            )
            if changed == 1:
                stored = await TaskRunHead.get(task_id)
                if stored is None:
                    raise RunStateConflict(
                        f"Task-run head disappeared after deletion for {task_id}"
                    )
                return stored
            await asyncio.sleep(0)
        raise RunStateConflict(f"Could not tombstone task-run head for {task_id}")

    async def _reserve_head(self, task_id: str, run_id: str) -> str | None:
        """Atomically reserve a task's portable primary-key head row."""
        for _ in range(MAX_CAS_ATTEMPTS):
            head = await TaskRunHead.get(task_id)
            if head is None:
                candidate = TaskRunHead(
                    task_id=task_id,
                    latest_run_id=run_id,
                    active_run_id=run_id,
                    version=1,
                )
                candidate.id = task_id
                try:
                    await _insert_with_explicit_id(TaskRunHead, candidate)
                    return None
                except Exception as exc:
                    # A different process may have inserted the primary-key
                    # row. Only treat it as that race when the row now exists.
                    if await TaskRunHead.get(task_id) is None:
                        raise
                    logger.debug("Task-run head creation race", exc_info=True)
                    await asyncio.sleep(0)
                    continue

            if head.deleted_at:
                raise TaskDeleted(f"Task {task_id} has been deleted")

            previous_latest = head.latest_run_id
            if head.active_run_id:
                active = await TaskRun.get(head.active_run_id)
                # A missing TaskRun does not prove this reservation is stale:
                # its creator may be paused between reserving the portable
                # head and inserting the run. Only the recovery pass may
                # clear a missing row after the reservation timeout.
                if active is None or active.status in _ACTIVE_STATUSES:
                    raise ActiveRunExists(
                        f"Task {task_id} already has an active run"
                    )

            changed = await TaskRunHead.update_one(
                {"id": task_id, "version": head.version},
                {
                    "latest_run_id": run_id,
                    "active_run_id": run_id,
                    "version": head.version + 1,
                },
            )
            if changed == 1:
                return previous_latest
            await asyncio.sleep(0)
        raise RunStateConflict(f"Could not reserve task-run head for {task_id}")

    async def recover_stale_reservations(
        self,
        *,
        now: datetime | None = None,
        reservation_timeout_seconds: float = (
            DEFAULT_HEAD_RESERVATION_TIMEOUT_SECONDS
        ),
    ) -> list[str]:
        """Release abandoned pre-insert reservations after a durable timeout."""
        if reservation_timeout_seconds < 0:
            raise ValueError("reservation_timeout_seconds cannot be negative")
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        else:
            current_time = current_time.astimezone(timezone.utc)
        cutoff = current_time - timedelta(seconds=reservation_timeout_seconds)
        released: list[str] = []

        for observed in await self._active_head_candidates():
            for _ in range(MAX_CAS_ATTEMPTS):
                head = await TaskRunHead.get(observed.id)
                if head is None or not head.active_run_id:
                    break
                active_run_id = head.active_run_id
                active = await TaskRun.get(active_run_id)
                if active is not None:
                    # Terminalization and active-head release are separate
                    # durable writes. Repair that harmless crash window too.
                    if active.status in _TERMINAL_STATUSES:
                        changed = await TaskRunHead.update_one(
                            {
                                "id": head.id,
                                "version": head.version,
                                "active_run_id": active_run_id,
                            },
                            {
                                "active_run_id": None,
                                "version": head.version + 1,
                            },
                        )
                        if changed == 1:
                            released.append(active_run_id)
                            break
                        await asyncio.sleep(0)
                        continue
                    break

                updated_at = head.updated_at
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                else:
                    updated_at = updated_at.astimezone(timezone.utc)
                if updated_at > cutoff:
                    break
                changed = await TaskRunHead.update_one(
                    {
                        "id": head.id,
                        "version": head.version,
                        "active_run_id": active_run_id,
                    },
                    {
                        "active_run_id": None,
                        "version": head.version + 1,
                    },
                )
                if changed == 1:
                    released.append(active_run_id)
                    break
                await asyncio.sleep(0)
        return released

    async def _active_head_candidates(self) -> list[TaskRunHead]:
        return await _recovery_model_query(
            TaskRunHead,
            relational_where=(
                "active_run_id IS NOT NULL AND active_run_id <> ''"
            ),
            mongo_conditions={
                "active_run_id": {"$exists": True, "$nin": [None, ""]}
            },
        )

    async def recovery_candidates(self) -> list[TaskRun]:
        """Load only queued/leased runs considered by the recovery state machine."""
        from odbms import DBMS

        dbms = getattr(DBMS.Database, "dbms", "")
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        statuses = {
            "queued": TaskRunStatus.QUEUED.value,
            "running": TaskRunStatus.RUNNING.value,
            "cancelling": TaskRunStatus.CANCELLING.value,
        }
        return await _recovery_model_query(
            TaskRun,
            relational_where=(
                "status IN ("
                + ", ".join(marker(name) for name in statuses)
                + ")"
            ),
            relational_params=statuses,
            mongo_conditions={"status": {"$in": list(statuses.values())}},
        )

    async def _outbox_candidates(self) -> list[TaskRun]:
        return await _recovery_model_query(
            TaskRun,
            relational_where=(
                "event_outbox IS NOT NULL "
                "AND TRIM(event_outbox) NOT IN ('', '[]', 'null')"
            ),
            mongo_conditions={
                "event_outbox": {"$exists": True, "$nin": [None, []]}
            },
        )

    async def completion_notification_candidates(
        self,
        *,
        now: datetime | None = None,
    ) -> list[TaskRun]:
        """Load pending or abandoned delivery claims without scanning history."""
        from odbms import DBMS

        current_time = now or datetime.now(timezone.utc)
        timestamp = _timestamp(current_time)
        dbms = getattr(DBMS.Database, "dbms", "")
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        params = {
            "pending": _NOTIFICATION_PENDING,
            "delivering": _NOTIFICATION_DELIVERING,
            "now": timestamp,
        }
        return await _recovery_model_query(
            TaskRun,
            relational_where=(
                "(completion_notification_state = "
                f"{marker('pending')} AND ("
                "completion_notification_next_at IS NULL OR "
                f"completion_notification_next_at <= {marker('now')})) OR ("
                f"completion_notification_state = {marker('delivering')} "
                "AND (completion_notification_expires_at IS NULL OR "
                f"completion_notification_expires_at <= {marker('now')}))"
            ),
            relational_params=params,
            mongo_conditions={
                "$or": [
                    {
                        "completion_notification_state": _NOTIFICATION_PENDING,
                        "$or": [
                            {"completion_notification_next_at": None},
                            {
                                "completion_notification_next_at": {
                                    "$lte": timestamp
                                }
                            },
                        ],
                    },
                    {
                        "completion_notification_state": _NOTIFICATION_DELIVERING,
                        "$or": [
                            {"completion_notification_expires_at": None},
                            {
                                "completion_notification_expires_at": {
                                    "$lte": timestamp
                                }
                            },
                        ],
                    },
                ]
            },
        )

    async def claim_completion_notification(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int = DEFAULT_NOTIFICATION_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> TaskRun | None:
        """Lease one terminal run's delivery token to a single process."""
        if not owner:
            raise ValueError("owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        else:
            current_time = current_time.astimezone(timezone.utc)

        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None or run.status not in _TERMINAL_STATUSES:
                return None
            if run.completion_notification_state == _NOTIFICATION_PENDING:
                next_at = _parse_timestamp(run.completion_notification_next_at)
                if next_at is not None and next_at > current_time:
                    return None
            elif run.completion_notification_state == _NOTIFICATION_DELIVERING:
                expires = _parse_timestamp(
                    run.completion_notification_expires_at
                )
                if expires is not None and expires > current_time:
                    return None
            else:
                return None

            changed = await TaskRun.update_one(
                {
                    "id": run_id,
                    "version": run.version,
                    "completion_notification_state": (
                        run.completion_notification_state
                    ),
                },
                {
                    "completion_notification_state": _NOTIFICATION_DELIVERING,
                    "completion_notification_owner": owner,
                    "completion_notification_expires_at": _timestamp(
                        current_time + timedelta(seconds=lease_seconds)
                    ),
                    "completion_notification_next_at": None,
                    "completion_notification_attempts": (
                        run.completion_notification_attempts + 1
                    ),
                    "version": run.version + 1,
                },
            )
            if changed == 1:
                claimed = await TaskRun.get(run_id)
                if claimed is None:
                    raise RunStateConflict(f"Task run {run_id} disappeared")
                return claimed
            await asyncio.sleep(0)
        raise RunStateConflict(
            f"Could not claim completion notification for task run {run_id}"
        )

    async def finish_completion_notification(
        self,
        run_id: str,
        *,
        owner: str,
        delivered: bool,
        now: datetime | None = None,
    ) -> TaskRun:
        """Acknowledge success or return a failed delivery to the outbox."""
        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None:
                raise RunStateConflict(f"Task run {run_id} does not exist")
            if run.completion_notification_state == _NOTIFICATION_DELIVERED:
                return run
            if (
                run.completion_notification_state != _NOTIFICATION_DELIVERING
                or run.completion_notification_owner != owner
            ):
                raise RunStateConflict(
                    f"Completion notification claim was lost for task run {run_id}"
                )
            patch: dict[str, Any] = {
                "completion_notification_owner": None,
                "completion_notification_expires_at": None,
                "version": run.version + 1,
            }
            if delivered:
                patch["completion_notification_state"] = _NOTIFICATION_DELIVERED
                patch["completion_notification_next_at"] = None
                patch["completion_notified_at"] = _timestamp(now)
            elif run.completion_notification_attempts >= MAX_NOTIFICATION_ATTEMPTS:
                patch["completion_notification_state"] = _NOTIFICATION_FAILED
                patch["completion_notification_next_at"] = None
            else:
                backoff_index = min(
                    max(run.completion_notification_attempts - 1, 0),
                    len(NOTIFICATION_BACKOFF_SECONDS) - 1,
                )
                retry_at = (now or datetime.now(timezone.utc)) + timedelta(
                    seconds=NOTIFICATION_BACKOFF_SECONDS[backoff_index]
                )
                patch["completion_notification_state"] = _NOTIFICATION_PENDING
                patch["completion_notification_next_at"] = _timestamp(retry_at)
            changed = await TaskRun.update_one(
                {
                    "id": run_id,
                    "version": run.version,
                    "completion_notification_state": _NOTIFICATION_DELIVERING,
                    "completion_notification_owner": owner,
                },
                patch,
            )
            if changed == 1:
                stored = await TaskRun.get(run_id)
                if stored is None:
                    raise RunStateConflict(f"Task run {run_id} disappeared")
                return stored
            await asyncio.sleep(0)
        raise RunStateConflict(
            f"Could not finish completion notification for task run {run_id}"
        )

    async def _restore_head_after_failed_create(
        self,
        task_id: str,
        run_id: str,
        previous_latest: str | None,
    ) -> None:
        for _ in range(MAX_CAS_ATTEMPTS):
            head = await TaskRunHead.get(task_id)
            if head is None or head.active_run_id != run_id:
                return
            changed = await TaskRunHead.update_one(
                {
                    "id": task_id,
                    "version": head.version,
                    "active_run_id": run_id,
                },
                {
                    "latest_run_id": previous_latest,
                    "active_run_id": None,
                    "version": head.version + 1,
                },
            )
            if changed == 1:
                return
            await asyncio.sleep(0)

    async def _release_active(self, task_id: str, run_id: str) -> None:
        for _ in range(MAX_CAS_ATTEMPTS):
            try:
                head = await TaskRunHead.get(task_id)
            except Exception:
                # Legacy/test stores can contain TaskRun rows before the
                # additive head table exists. There is no reservation to
                # release in that case.
                return
            if head is None or head.active_run_id != run_id:
                return
            changed = await TaskRunHead.update_one(
                {
                    "id": task_id,
                    "version": head.version,
                    "active_run_id": run_id,
                },
                {"active_run_id": None, "version": head.version + 1},
            )
            if changed == 1:
                return
            await asyncio.sleep(0)
        raise RunStateConflict(f"Could not release task-run head for {task_id}")

    async def latest_run(self, task_id: str) -> TaskRun | None:
        try:
            head = await TaskRunHead.get(task_id)
        except Exception:
            # Additive rollout: a reader may briefly precede schema creation,
            # and legacy/unit stores may not expose the head table at all.
            head = None
        if head is not None and head.latest_run_id:
            run = await TaskRun.get(head.latest_run_id)
            if run is not None:
                return run
        legacy = await TaskRun.find({"task_id": task_id})
        return max(
            legacy,
            key=lambda item: (item.json().get("created_at") or "", str(item.id)),
            default=None,
        )

    async def active_run(self, task_id: str) -> TaskRun | None:
        try:
            head = await TaskRunHead.get(task_id)
        except Exception:
            head = None
        if head is not None and head.active_run_id:
            run = await TaskRun.get(head.active_run_id)
            if run is not None and run.status in _ACTIVE_STATUSES:
                return run
        legacy = await TaskRun.find({"task_id": task_id})
        active = [run for run in legacy if run.status in _ACTIVE_STATUSES]
        return max(
            active,
            key=lambda item: (item.json().get("created_at") or "", str(item.id)),
            default=None,
        )

    async def create_queued(
        self,
        *,
        task_id: str,
        requested_by: str | None = None,
        actor_key: str | None = None,
        authority_kind: str = "system",
        authority_id: str | None = None,
        acl_team_id: str | None = None,
        acl_agent_ids: list[str] | None = None,
        callback_url: str | None = None,
        callback_key_id: str | None = None,
        resume_from_run_id: str | None = None,
        budget: dict[str, Any] | None = None,
    ) -> TaskRun:
        # Reject before reserving a head or persisting a queued row. Letting
        # MongoDB reach worker claim would strand a RUNNING row because the
        # durable step fence depends on relational cross-table predicates.
        _require_supported_durable_backend()
        await self._ensure_indexes()
        # Upgrade-safe admission: seed a missing head from bounded historical
        # rows before reserving a new id.  This closes the rollout window where
        # pre-head active runs would otherwise be invisible to _reserve_head.
        await self.reconcile_task_head(task_id)
        run = TaskRun(
            task_id=task_id,
            status=TaskRunStatus.QUEUED,
            queued_at=None,
            requested_by=requested_by,
            actor_key=actor_key,
            authority_kind=authority_kind,
            authority_id=authority_id,
            **run_acl_snapshot(team_id=acl_team_id, agent_ids=acl_agent_ids),
            callback_url=callback_url,
            callback_key_id=callback_key_id,
            resume_from_run_id=resume_from_run_id,
            budget=dict(budget or {}),
            version=0,
            next_event_sequence=0,
            event_outbox=[],
        )
        run.id = str(uuid.uuid4())
        previous_latest = await self._reserve_head(task_id, run.id)
        try:
            # Read as close to the durable insert as possible so a paused head
            # reservation cannot make a newly inserted run look queue-stale.
            run.queued_at = await _database_timestamp()
            await _insert_with_explicit_id(TaskRun, run)
        except Exception:
            await self._restore_head_after_failed_create(
                task_id,
                run.id,
                previous_latest,
            )
            raise
        # Recovery may have expired an abnormally long-running reservation
        # while this creator was paused. Do not let that stale creator publish
        # an unheaded second active run after the reservation was reassigned.
        head = await TaskRunHead.get(task_id)
        if head is None or head.active_run_id != run.id:
            await TaskRun.delete_many({"id": run.id})
            raise RunStateConflict(
                f"Task-run head reservation was lost for {task_id}"
            )
        return run

    async def claim(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int = 60,
    ) -> LeaseClaim | None:
        if not owner:
            raise ValueError("owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None or run.status != TaskRunStatus.QUEUED:
                return None

            generation = run.lease_generation + 1
            changed = await self._claim_update(
                run,
                owner=owner,
                generation=generation,
                lease_seconds=lease_seconds,
            )
            if changed == 1:
                return LeaseClaim(
                    run_id=run_id,
                    owner=owner,
                    generation=generation,
                )
            await asyncio.sleep(0)
        return None

    async def _claim_update(
        self,
        run: TaskRun,
        *,
        owner: str,
        generation: int,
        lease_seconds: int,
    ) -> int:
        """Claim a queued run with one DB-clock relational UPDATE."""
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            # Keep model-only fakes usable; production enqueue already rejects
            # every unsupported durable backend before a row can be created.
            now = datetime.now(timezone.utc)
            return await TaskRun.update_one(
                {
                    "id": run.id,
                    "status": TaskRunStatus.QUEUED.value,
                    "version": run.version,
                },
                {
                    "status": TaskRunStatus.RUNNING.value,
                    "lease_owner": owner,
                    "lease_generation": generation,
                    "heartbeat_at": _timestamp(now),
                    "lease_expires_at": _timestamp(
                        now + timedelta(seconds=lease_seconds)
                    ),
                    "started_at": run.started_at or _timestamp(now),
                    "version": run.version + 1,
                },
            )

        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        now = _database_time_expression(dbms)
        expiry = _database_lease_expiry_expression(
            dbms,
            marker("lease_seconds"),
        )
        params = {
            "run_id": run.id,
            "queued_status": TaskRunStatus.QUEUED.value,
            "running_status": TaskRunStatus.RUNNING.value,
            "run_version": run.version,
            "next_version": run.version + 1,
            "lease_owner": owner,
            "lease_generation": generation,
            "lease_seconds": lease_seconds,
        }
        cursor = await database.query(
            f"UPDATE {TaskRun.table_name()} SET "
            f"status = {marker('running_status')}, "
            f"lease_owner = {marker('lease_owner')}, "
            f"lease_generation = {marker('lease_generation')}, "
            f"heartbeat_at = {now}, "
            f"lease_expires_at = {expiry}, "
            f"started_at = COALESCE(started_at, {now}), "
            f"version = {marker('next_version')}, "
            f"updated_at = {_database_updated_at_expression(dbms)} "
            f"WHERE id = {marker('run_id')} "
            f"AND status = {marker('queued_status')} "
            f"AND version = {marker('run_version')}",
            params,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def heartbeat(
        self,
        run_id: str,
        *,
        claim: LeaseClaim,
        lease_seconds: int = 60,
    ) -> TaskRun:
        """Renew only the exact live lease represented by ``claim``."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        from odbms import DBMS

        dbms = getattr(DBMS.Database, "dbms", "")
        relational = dbms in ("sqlite", "postgresql", "mysql")
        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if (
                run is None
                or run.status not in _LEASED_STATUSES
                or claim is None
                or claim.run_id != run_id
                or claim.owner != run.lease_owner
                or claim.generation != run.lease_generation
                or (not relational and not _lease_is_live(run))
            ):
                raise LeaseLost(f"Lease lost for task run {run_id}")

            changed = await self._heartbeat_update(
                run,
                claim=claim,
                lease_seconds=lease_seconds,
            )
            if changed == 1:
                stored = await TaskRun.get(run_id)
                if stored is None:
                    raise LeaseLost(f"Lease lost for task run {run_id}")
                return stored
            await self._require_step_write(run_id, claim)
            await asyncio.sleep(0)

        raise RunStateConflict(f"Task run {run_id} changed too frequently")

    async def _heartbeat_update(
        self,
        run: TaskRun,
        *,
        claim: LeaseClaim,
        lease_seconds: int,
    ) -> int:
        """Renew an exact live generation using one DB-clock UPDATE."""
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            now = datetime.now(timezone.utc)
            return await TaskRun.update_one(
                {
                    "id": run.id,
                    "status": run.status.value,
                    "version": run.version,
                    "lease_owner": claim.owner,
                    "lease_generation": claim.generation,
                },
                {
                    "heartbeat_at": _timestamp(now),
                    "lease_expires_at": _timestamp(
                        now + timedelta(seconds=lease_seconds)
                    ),
                    "version": run.version + 1,
                },
            )

        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        now = _database_time_expression(dbms)
        expiry = _database_lease_expiry_expression(
            dbms,
            marker("lease_seconds"),
        )
        params = {
            "run_id": run.id,
            "run_status": run.status.value,
            "run_version": run.version,
            "next_version": run.version + 1,
            "lease_owner": claim.owner,
            "lease_generation": claim.generation,
            "lease_seconds": lease_seconds,
        }
        cursor = await database.query(
            f"UPDATE {TaskRun.table_name()} SET "
            f"heartbeat_at = {now}, "
            f"lease_expires_at = {expiry}, "
            f"version = {marker('next_version')}, "
            f"updated_at = {_database_updated_at_expression(dbms)} "
            f"WHERE id = {marker('run_id')} "
            f"AND status = {marker('run_status')} "
            f"AND version = {marker('run_version')} "
            f"AND lease_owner = {marker('lease_owner')} "
            f"AND lease_generation = {marker('lease_generation')} "
            f"AND {_lease_expiry_predicate(dbms, 'lease_expires_at')}",
            params,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def _fenced_run_update(
        self,
        run: TaskRun,
        *,
        claim: LeaseClaim,
        patch: dict[str, Any],
    ) -> int:
        """CAS a run mutation only while its lease is live on the DB clock."""
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            raise RunStateConflict(
                "Atomic run lease fencing requires a relational database"
            )
        values = TaskRun.normalise(
            {
                **patch,
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
            "params",
        )
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        params = {f"set_{key}": value for key, value in values.items()}
        params.update(
            run_id=run.id,
            run_status=run.status.value,
            run_version=run.version,
            lease_owner=claim.owner,
            lease_generation=claim.generation,
        )
        assignments = ", ".join(
            f"{column} = {marker(f'set_{column}')}" for column in values
        )
        cursor = await database.query(
            f"UPDATE {TaskRun.table_name()} SET {assignments} "
            f"WHERE id = {marker('run_id')} "
            f"AND status = {marker('run_status')} "
            f"AND version = {marker('run_version')} "
            f"AND lease_owner = {marker('lease_owner')} "
            f"AND lease_generation = {marker('lease_generation')} "
            f"AND {_lease_expiry_predicate(dbms, 'lease_expires_at')}",
            params,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def mutate(
        self,
        run_id: str,
        *,
        claim: LeaseClaim | None,
        updates: dict[str, Any],
        expected_statuses: Iterable[TaskRunStatus | str] | None = None,
        event: dict[str, Any] | None = None,
    ) -> TaskRun:
        run, _ = await self._mutate(
            run_id,
            claim=claim,
            updates=updates,
            expected_statuses=expected_statuses,
            event=event,
        )
        return run

    async def persist_usage(
        self,
        run_id: str,
        *,
        claim: LeaseClaim | None,
        snapshot: dict[str, Any],
    ) -> TaskRun:
        """Lease-fence a cumulative usage snapshot under the run CAS.

        Usage counters are cumulative while reservation fields are current
        gauges. Each retry merges cumulative values forward and replaces the
        gauges from the lease-holder's serialized ledger snapshot before
        attempting the exact lease/version CAS.
        """
        candidate = _normalise_usage_snapshot(dict(snapshot))
        database_clock = _uses_database_lease_clock()
        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None:
                raise RunStateConflict(f"Task run {run_id} does not exist")
            if (
                run.status not in _LEASED_STATUSES
                or claim is None
                or claim.run_id != run_id
                or claim.owner != run.lease_owner
                or claim.generation != run.lease_generation
                or (not database_clock and not _lease_is_live(run))
            ):
                raise LeaseLost(f"Lease lost for task run {run_id}")

            usage = _merge_usage(run.usage, candidate)
            changed = await self._fenced_run_update(
                run,
                claim=claim,
                patch={
                    "usage": usage,
                    "version": run.version + 1,
                },
            )
            if changed == 1:
                stored = await TaskRun.get(run_id)
                if stored is None:
                    raise RunStateConflict(f"Task run {run_id} disappeared")
                return stored
            await self._require_step_write(run_id, claim)
            await asyncio.sleep(0)

        raise RunStateConflict(f"Task run {run_id} changed too frequently")

    async def _require_step_write(
        self,
        run_id: str,
        claim: LeaseClaim | None,
    ) -> TaskRun:
        """Fence an authoritative step write with the run's current lease."""
        run = await TaskRun.get(run_id)
        if run is None:
            raise RunStateConflict(f"Task run {run_id} does not exist")
        if (
            run.status not in _LEASED_STATUSES
            or claim is None
            or claim.run_id != run_id
            or claim.owner != run.lease_owner
            or claim.generation != run.lease_generation
        ):
            raise LeaseLost(f"Lease lost for task run {run_id}")
        if not await _database_claim_is_live(run, claim):
            raise LeaseLost(f"Lease lost for task run {run_id}")
        return run

    async def hydrate_plan(
        self,
        run_id: str,
        *,
        include_results: bool = True,
    ) -> list[dict[str, Any]]:
        """Read the compatibility plan from rows, falling back for old runs."""
        run = await TaskRun.get(run_id)
        if run is None:
            raise RunStateConflict(f"Task run {run_id} does not exist")

        rows = await TaskRunStep.find({"run_id": run_id})
        if rows:
            rows.sort(key=lambda row: row.step_index)
            plan = [row.to_plan_entry() for row in rows]
        else:
            plan = copy.deepcopy(run.plan or [])

        if not include_results:
            for entry in plan:
                entry.pop("result", None)
                entry.pop("result_data", None)
        return plan

    async def _converge_plan(
        self,
        run_id: str,
        *,
        claim: LeaseClaim | None,
    ) -> TaskRun:
        """Persist row projection and verify no concurrent row change was lost."""
        for _ in range(MAX_CAS_ATTEMPTS):
            await self._require_step_write(run_id, claim)
            desired = await self.hydrate_plan(run_id)
            run = await TaskRun.get(run_id)
            if run is None:
                raise RunStateConflict(f"Task run {run_id} does not exist")

            if run.plan != desired:
                await self.mutate(
                    run_id,
                    claim=claim,
                    updates={"plan": desired},
                    expected_statuses=_LEASED_STATUSES,
                )

            fresh = await TaskRun.get(run_id)
            current = await self.hydrate_plan(run_id)
            if fresh is not None and fresh.plan == current:
                return fresh
            await asyncio.sleep(0)
        raise RunStateConflict(
            f"Task run {run_id} step projection changed too frequently"
        )

    async def compile_steps(
        self,
        run_id: str,
        plan: list[dict[str, Any]],
        *,
        claim: LeaseClaim | None,
    ) -> list[TaskRunStep]:
        """Insert one authoritative row per compiled plan entry.

        Existing indexes are preserved so a retried compile can finish a
        partially inserted plan without rewriting rows that already exist.
        """
        run = await self._require_step_write(run_id, claim)
        await self._ensure_indexes()

        entries: dict[int, dict[str, Any]] = {}
        for position, raw in enumerate(plan):
            if not isinstance(raw, dict):
                raise TypeError("Compiled plan entries must be mappings")
            index = int(raw.get("index", raw.get("step_index", position)))
            if index in entries:
                raise ValueError(f"Duplicate task-run step index: {index}")
            entries[index] = raw

        existing = await TaskRunStep.find({"run_id": run_id})
        by_index = {row.step_index: row for row in existing}
        extras = set(by_index) - set(entries)
        if extras:
            raise RunStateConflict(
                f"Task run {run_id} already contains unexpected steps: "
                f"{sorted(extras)}"
            )

        for position, index in enumerate(sorted(entries)):
            candidate = _step_from_plan_entry(run, entries[index], position)
            if index in by_index:
                _assert_same_step_definition(by_index[index], candidate)
                continue
            await self._require_step_write(run_id, claim)
            if claim is None:
                raise LeaseLost(f"Lease lost for task run {run_id}")
            try:
                inserted = await self._fenced_step_insert(candidate, claim=claim)
            except Exception:
                # A concurrent idempotent compiler may have won the unique
                # (run_id, step_index) insert. Accept it only while this worker
                # still holds the lease and its immutable definition matches.
                await self._require_step_write(run_id, claim)
                stored = await TaskRunStep.find_one(
                    {"run_id": run_id, "step_index": index}
                )
                if stored is None:
                    raise
                _assert_same_step_definition(stored, candidate)
                by_index[index] = stored
                continue

            if inserted != 1:
                await self._require_step_write(run_id, claim)
                stored = await TaskRunStep.find_one(
                    {"run_id": run_id, "step_index": index}
                )
                if stored is None:
                    raise RunStateConflict(
                        f"Could not persist task run {run_id} step {index}"
                    )
                _assert_same_step_definition(stored, candidate)
                by_index[index] = stored
                continue

            stored = await TaskRunStep.get(candidate.id)
            if stored is None:
                raise RunStateConflict(f"Task run {run_id} step {index} disappeared")
            by_index[index] = stored

        await self._converge_plan(run_id, claim=claim)
        rows = await TaskRunStep.find({"run_id": run_id})
        return sorted(rows, key=lambda row: row.step_index)

    async def _fenced_step_insert(
        self,
        candidate: TaskRunStep,
        *,
        claim: LeaseClaim,
    ) -> int:
        """Insert a step only while the exact run lease remains live."""
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            raise RunStateConflict(
                "Atomic task-step lease fencing requires a relational database"
            )

        candidate.id = candidate.id or str(uuid.uuid4())
        values = TaskRunStep.normalise(
            candidate.model_dump(mode="json"),
            "params",
        )
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        params = {f"step_{key}": value for key, value in values.items()}
        params.update(
            run_id=claim.run_id,
            lease_owner=claim.owner,
            lease_generation=claim.generation,
            running_status=TaskRunStatus.RUNNING.value,
            cancelling_status=TaskRunStatus.CANCELLING.value,
        )
        columns = ", ".join(values)
        selected = ", ".join(marker(f"step_{column}") for column in values)
        lock_clause = "" if dbms == "sqlite" else " FOR UPDATE"
        cursor = await database.query(
            f"INSERT INTO {TaskRunStep.table_name()} ({columns}) "
            f"SELECT {selected} WHERE EXISTS (SELECT 1 FROM taskruns "
            f"WHERE taskruns.id = {marker('run_id')} "
            f"AND taskruns.status IN ({marker('running_status')}, "
            f"{marker('cancelling_status')}) "
            f"AND taskruns.lease_owner = {marker('lease_owner')} "
            f"AND taskruns.lease_generation = {marker('lease_generation')} "
            f"AND {_lease_expiry_predicate(dbms, 'taskruns.lease_expires_at')}"
            f"{lock_clause})",
            params,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def transition_step(
        self,
        run_id: str,
        step_index: int,
        *,
        claim: LeaseClaim | None,
        updates: dict[str, Any],
        expected_statuses: Iterable[TaskRunStepStatus | str] | None = None,
    ) -> TaskRunStep:
        """CAS one authoritative step row without rewriting the full plan."""
        await self._ensure_indexes()
        expected = _step_status_set(expected_statuses)
        patch = _step_update_patch(dict(updates))

        changed = False
        for _ in range(MAX_CAS_ATTEMPTS):
            await self._require_step_write(run_id, claim)
            row = await TaskRunStep.find_one(
                {"run_id": run_id, "step_index": step_index}
            )
            if row is None:
                raise RunStateConflict(
                    f"Task run {run_id} has no step {step_index}"
                )
            current_status = row.status.value
            if expected is not None and current_status not in expected:
                raise RunStateConflict(
                    f"Task run {run_id} step {step_index} is {current_status}, "
                    f"expected {sorted(expected)}"
                )
            target_status = str(patch.get("status", current_status))
            _require_legal_step_transition(current_status, target_status)
            if not patch:
                changed = True
                break

            if claim is None:
                raise LeaseLost(f"Lease lost for task run {run_id}")
            updated = await self._fenced_step_update(
                row,
                current_status=current_status,
                claim=claim,
                patch=patch,
            )
            if updated == 1:
                changed = True
                break
            # Distinguish an ordinary step CAS race from recovery/cancellation
            # advancing the run fence while this worker was preparing its write.
            await self._require_step_write(run_id, claim)
            await asyncio.sleep(0)

        if not changed:
            raise RunStateConflict(
                f"Task run {run_id} step {step_index} changed too frequently"
            )

        stored = await TaskRunStep.find_one(
            {"run_id": run_id, "step_index": step_index}
        )
        if stored is None:
            raise RunStateConflict(
                f"Task run {run_id} step {step_index} disappeared"
            )
        return stored

    async def backfill_step_runtime(
        self,
        run_id: str,
        step_index: int,
        *,
        claim: LeaseClaim | None,
        agent_name: str,
        runtime_snapshot: AgentRuntimeSnapshot | Mapping[str, Any],
    ) -> TaskRunStep:
        """Populate a legacy step runtime exactly once under the live lease."""
        snapshot = AgentRuntimeSnapshot.model_validate(runtime_snapshot)
        resolved_name = str(agent_name).strip()
        if not resolved_name:
            raise ValueError("agent_name is required")
        if resolved_name != snapshot.name:
            raise ValueError("agent_name must match the runtime snapshot")

        for _ in range(MAX_CAS_ATTEMPTS):
            await self._require_step_write(run_id, claim)
            row = await TaskRunStep.find_one(
                {"run_id": run_id, "step_index": step_index}
            )
            if row is None:
                raise RunStateConflict(
                    f"Task run {run_id} has no step {step_index}"
                )
            if row.runtime_snapshot is not None:
                if (
                    row.agent_name == resolved_name
                    and row.runtime_snapshot == snapshot
                ):
                    return row
                raise RunStateConflict(
                    f"Task run {run_id} step {step_index} runtime snapshot "
                    "is immutable"
                )
            if claim is None:
                raise LeaseLost(f"Lease lost for task run {run_id}")

            updated = await self._fenced_step_update(
                row,
                current_status=row.status.value,
                claim=claim,
                patch={
                    "agent_name": resolved_name,
                    "runtime_snapshot": snapshot.model_dump(mode="json"),
                },
                require_runtime_snapshot_missing=True,
            )
            if updated == 1:
                stored = await TaskRunStep.find_one(
                    {"run_id": run_id, "step_index": step_index}
                )
                if stored is None:
                    raise RunStateConflict(
                        f"Task run {run_id} step {step_index} disappeared"
                    )
                return stored

            await self._require_step_write(run_id, claim)
            await asyncio.sleep(0)

        raise RunStateConflict(
            f"Task run {run_id} step {step_index} changed too frequently"
        )

    async def _fenced_step_update(
        self,
        row: TaskRunStep,
        *,
        current_status: str,
        claim: LeaseClaim,
        patch: dict[str, Any],
        require_runtime_snapshot_missing: bool = False,
    ) -> int:
        """Update a step only while its exact run lease is still authoritative.

        The run predicate and step mutation deliberately share one SQL
        statement. A separate preflight read cannot fence another process:
        recovery could advance ``lease_generation`` between that read and the
        step-row update.
        """
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            raise RunStateConflict(
                "Atomic task-step lease fencing requires a relational database"
            )

        values = TaskRunStep.normalise(
            {
                **patch,
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
            "params",
        )
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        params = {f"set_{key}": value for key, value in values.items()}
        params.update(
            step_id=row.id,
            step_status=current_status,
            run_id=claim.run_id,
            lease_owner=claim.owner,
            lease_generation=claim.generation,
            running_status=TaskRunStatus.RUNNING.value,
            cancelling_status=TaskRunStatus.CANCELLING.value,
        )
        assignments = ", ".join(
            f"{column} = {marker(f'set_{column}')}" for column in values
        )
        runtime_snapshot_clause = (
            "AND runtime_snapshot IS NULL "
            if require_runtime_snapshot_missing
            else ""
        )
        lock_clause = "" if dbms == "sqlite" else " FOR UPDATE"
        cursor = await database.query(
            f"UPDATE {TaskRunStep.table_name()} SET {assignments} "
            f"WHERE id = {marker('step_id')} "
            f"AND status = {marker('step_status')} "
            f"{runtime_snapshot_clause}"
            "AND EXISTS (SELECT 1 FROM taskruns "
            f"WHERE taskruns.id = {marker('run_id')} "
            f"AND taskruns.status IN ({marker('running_status')}, "
            f"{marker('cancelling_status')}) "
            f"AND taskruns.lease_owner = {marker('lease_owner')} "
            f"AND taskruns.lease_generation = {marker('lease_generation')} "
            f"AND {_lease_expiry_predicate(dbms, 'taskruns.lease_expires_at')}"
            f"{lock_clause})",
            params,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def record_metric(
        self,
        run_id: str,
        *,
        claim: LeaseClaim | None,
        metric: TaskRunPhaseMetric,
    ) -> TaskRunPhaseMetric:
        """Insert an immutable phase metric only under the exact live lease."""
        if metric.run_id != run_id:
            raise ValueError("metric.run_id must match run_id")
        await self._require_step_write(run_id, claim)
        if claim is None:
            raise LeaseLost(f"Lease lost for task run {run_id}")

        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        if dbms not in ("sqlite", "postgresql", "mysql"):
            raise RunStateConflict(
                "Atomic task metric lease fencing requires a relational database"
            )

        metric.id = metric.id or str(uuid.uuid4())
        values = TaskRunPhaseMetric.normalise(
            metric.model_dump(mode="json"),
            "params",
        )
        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        params = {f"metric_{key}": value for key, value in values.items()}
        params.update(
            run_id=claim.run_id,
            lease_owner=claim.owner,
            lease_generation=claim.generation,
            running_status=TaskRunStatus.RUNNING.value,
            cancelling_status=TaskRunStatus.CANCELLING.value,
        )
        columns = ", ".join(values)
        selected = ", ".join(marker(f"metric_{column}") for column in values)
        lock_clause = "" if dbms == "sqlite" else " FOR UPDATE"
        cursor = await database.query(
            f"INSERT INTO {TaskRunPhaseMetric.table_name()} ({columns}) "
            f"SELECT {selected} WHERE EXISTS (SELECT 1 FROM taskruns "
            f"WHERE taskruns.id = {marker('run_id')} "
            f"AND taskruns.status IN ({marker('running_status')}, "
            f"{marker('cancelling_status')}) "
            f"AND taskruns.lease_owner = {marker('lease_owner')} "
            f"AND taskruns.lease_generation = {marker('lease_generation')} "
            f"AND {_lease_expiry_predicate(dbms, 'taskruns.lease_expires_at')}"
            f"{lock_clause})",
            params,
        )
        inserted = int(getattr(cursor, "rowcount", 0) or 0)
        if inserted != 1:
            await self._require_step_write(run_id, claim)
            raise RunStateConflict(f"Could not persist metric for task run {run_id}")

        stored = await TaskRunPhaseMetric.get(metric.id)
        if stored is None:
            raise RunStateConflict(f"Task metric {metric.id} disappeared")
        return stored

    async def seed_resume_steps(
        self,
        run_id: str,
        source_run_id: str,
        *,
        claim: LeaseClaim | None,
    ) -> list[TaskRunStep]:
        """Seed a resumed run, preferring typed rows over a legacy plan."""
        target = await self._require_step_write(run_id, claim)
        source = await TaskRun.get(source_run_id)
        if source is None:
            raise RunStateConflict(f"Task run {source_run_id} does not exist")
        if source.id == target.id:
            raise RunStateConflict("A task run cannot resume from itself")
        if source.task_id != target.task_id:
            raise RunStateConflict("Resume source belongs to a different task")

        source_rows = await TaskRunStep.find({"run_id": source_run_id})
        source_rows.sort(key=lambda row: row.step_index)
        if source_rows:
            entries = [
                {
                    "index": row.step_index,
                    "title": row.title,
                    "description": row.description,
                    "expected_output": row.expected_output,
                    "verification_criteria": row.verification_criteria,
                    "agent_name": row.agent_name,
                    "dependencies": list(row.dependencies),
                    "required_tools": row.required_tools,
                    "runtime_snapshot": (
                        row.runtime_snapshot.model_dump(mode="json")
                        if row.runtime_snapshot is not None
                        else None
                    ),
                    "status": row.status.value,
                    "attempts": row.attempts,
                    "result": (
                        row.result.model_dump(mode="json")
                        if row.result is not None
                        else None
                    ),
                    "gate": row.gate,
                    "error": row.error,
                    "started_at": row.started_at,
                    "completed_at": row.completed_at,
                }
                for row in source_rows
            ]
        else:
            entries = copy.deepcopy(source.plan or [])

        for entry in entries:
            if _step_status_value(
                entry.get("status") or TaskRunStepStatus.PENDING
            ) != TaskRunStepStatus.DONE.value:
                entry.update(
                    status=TaskRunStepStatus.PENDING.value,
                    attempts=0,
                    result=None,
                    gate=None,
                    error=None,
                    started_at=None,
                    completed_at=None,
                )

        return await self.compile_steps(run_id, entries, claim=claim)

    async def _mutate(
        self,
        run_id: str,
        *,
        claim: LeaseClaim | None,
        updates: dict[str, Any],
        expected_statuses: Iterable[TaskRunStatus | str] | None,
        event: dict[str, Any] | None,
    ) -> tuple[TaskRun, dict[str, Any] | None]:
        expected = _status_set(expected_statuses)
        updates = _run_update_patch(dict(updates))
        database_clock = _uses_database_lease_clock()

        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None:
                raise RunStateConflict(f"Task run {run_id} does not exist")

            current_status = run.status.value
            if expected is not None and current_status not in expected:
                raise RunStateConflict(
                    f"Task run {run_id} is {current_status}, expected {sorted(expected)}"
                )

            if claim is not None:
                if (
                    claim.run_id != run_id
                    or claim.owner != run.lease_owner
                    or claim.generation != run.lease_generation
                    or (
                        run.status in _LEASED_STATUSES
                        and not database_clock
                        and not _lease_is_live(run)
                    )
                ):
                    raise LeaseLost(f"Lease lost for task run {run_id}")
            elif run.status in _LEASED_STATUSES:
                raise LeaseLost(f"Lease lost for task run {run_id}")

            # Terminal lifecycle and execution data are immutable. The lease
            # fields remain as the final fencing token so former/stale workers
            # cannot append events or rewrite results after recovery wins.
            if run.status in _TERMINAL_STATUSES:
                raise RunStateConflict(f"Task run {run_id} is terminal")

            patch = dict(updates)
            target_status = patch.get("status")
            target_status_value = (
                target_status.value
                if isinstance(target_status, TaskRunStatus)
                else target_status
            )
            if (
                target_status_value
                in {status.value for status in _TERMINAL_STATUSES}
                and run.completion_notification_state is None
            ):
                patch["completion_notification_state"] = (
                    _terminal_notification_state(run)
                )
            patch["version"] = run.version + 1
            envelope = None
            if event is not None:
                sequence = run.next_event_sequence + 1
                envelope = _event_envelope(run_id, sequence, event)
                patch["next_event_sequence"] = sequence
                patch["event_outbox"] = [*run.event_outbox, envelope]

            query: dict[str, Any] = {"id": run_id, "version": run.version}
            if expected is not None and len(expected) == 1:
                query["status"] = next(iter(expected))
            if run.status in _LEASED_STATUSES:
                query["lease_owner"] = run.lease_owner
                query["lease_generation"] = run.lease_generation

            if run.status in _LEASED_STATUSES:
                assert claim is not None
                changed = await self._fenced_run_update(
                    run,
                    claim=claim,
                    patch=patch,
                )
            else:
                changed = await TaskRun.update_one(query, patch)
            if changed != 1:
                if run.status in _LEASED_STATUSES:
                    await self._require_step_write(run_id, claim)
                await asyncio.sleep(0)
                continue

            terminal = target_status_value in {
                status.value for status in _TERMINAL_STATUSES
            }
            if terminal:
                # The lifecycle CAS is authoritative. Release ownership before
                # touching the event store so delivery failure cannot strand a
                # completed run as the task's active head.
                await self._release_active(run.task_id, run.id)
            if envelope is not None:
                if terminal:
                    await self._flush_outbox_best_effort(run_id)
                else:
                    await self.flush_outbox(run_id)
            stored = await TaskRun.get(run_id)
            if stored is None:
                raise RunStateConflict(f"Task run {run_id} disappeared")
            if stored.status in _TERMINAL_STATUSES and not terminal:
                await self._release_active(stored.task_id, stored.id)
            return stored, envelope

        raise RunStateConflict(f"Task run {run_id} changed too frequently")

    async def emit_event(
        self,
        run_id: str,
        *,
        claim: LeaseClaim | None,
        kind: str,
        session_id: str | None = None,
        step_index: int | None = None,
        agent_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> TaskRunEvent:
        _, envelope = await self._mutate(
            run_id,
            claim=claim,
            updates={},
            expected_statuses=None,
            event={
                "kind": kind,
                "session_id": session_id,
                "step_index": step_index,
                "agent_name": agent_name,
                "data": data or {},
            },
        )
        assert envelope is not None
        stored = await TaskRunEvent.find_one(
            {"run_id": run_id, "sequence": envelope["sequence"]}
        )
        return stored or TaskRunEvent(**envelope)

    async def flush_outbox(self, run_id: str) -> list[TaskRunEvent]:
        delivered: list[TaskRunEvent] = []
        attempts = 0
        while attempts < MAX_CAS_ATTEMPTS * 4:
            attempts += 1
            run = await TaskRun.get(run_id)
            if run is None or not run.event_outbox:
                return delivered

            envelope = dict(run.event_outbox[0])
            sequence = int(envelope["sequence"])
            persisted = await TaskRunEvent.find_one(
                {"run_id": run_id, "sequence": sequence}
            )
            if persisted is None:
                candidate = TaskRunEvent(**envelope)
                try:
                    await candidate.save()
                    persisted = candidate
                except Exception:
                    # Insert may have succeeded in another flusher first. A
                    # unique (run_id, sequence) row makes that equivalent to
                    # success; any other insert failure remains fatal.
                    persisted = await TaskRunEvent.find_one(
                        {"run_id": run_id, "sequence": sequence}
                    )
                    if persisted is None:
                        raise

            changed = await TaskRun.update_one(
                {"id": run_id, "version": run.version},
                {
                    "event_outbox": list(run.event_outbox[1:]),
                    "version": run.version + 1,
                },
            )
            if changed == 1:
                delivered.append(persisted)
            else:
                await asyncio.sleep(0)
        raise RunStateConflict(f"Could not drain event outbox for task run {run_id}")

    async def _flush_outbox_best_effort(self, run_id: str) -> list[TaskRunEvent]:
        """Attempt delivery without masking an already committed transition."""
        try:
            return await self.flush_outbox(run_id)
        except Exception:
            logger.warning(
                "Task run %s committed with pending event outbox delivery",
                run_id,
                exc_info=True,
            )
            return []

    async def recover_outboxes(self) -> list[str]:
        """Drain every stranded event envelope after process restart."""
        recovered: list[str] = []
        for run in await self._outbox_candidates():
            if not run.event_outbox:
                continue
            try:
                await self.flush_outbox(run.id)
            except Exception:
                logger.warning(
                    "Task run %s retains a poison event outbox",
                    run.id,
                    exc_info=True,
                )
            else:
                recovered.append(run.id)
        return recovered

    async def _recover_terminal_update(
        self,
        observed: TaskRun,
        *,
        patch: dict[str, Any],
        queue_timeout_seconds: float,
    ) -> int:
        """Commit terminal recovery only if DB time still proves staleness."""
        from odbms import DBMS

        database = DBMS.Database
        dbms = getattr(database, "dbms", "")
        query: dict[str, Any] = {
            "id": observed.id,
            "status": observed.status.value,
            "version": observed.version,
        }
        if observed.status in _LEASED_STATUSES:
            query["lease_generation"] = observed.lease_generation
            if observed.lease_owner is not None:
                query["lease_owner"] = observed.lease_owner

        # Preserve lightweight model fakes and the existing Mongo adapter.
        # Relational backends use the stronger single-statement DB-clock fence.
        if dbms not in ("sqlite", "postgresql", "mysql"):
            return await TaskRun.update_one(query, patch)

        marker = (
            (lambda name: f":{name}")
            if dbms == "sqlite"
            else (lambda name: f"%({name})s")
        )
        values = TaskRun.normalise(
            {
                **patch,
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
            "params",
        )
        params = {f"set_{key}": value for key, value in values.items()}
        params.update(
            run_id=observed.id,
            run_status=observed.status.value,
            run_version=observed.version,
            queue_timeout_seconds=queue_timeout_seconds,
        )
        assignments = ", ".join(
            f"{column} = {marker(f'set_{column}')}" for column in values
        )
        predicates = [
            f"id = {marker('run_id')}",
            f"status = {marker('run_status')}",
            f"version = {marker('run_version')}",
        ]
        if observed.status in _LEASED_STATUSES:
            params["lease_generation"] = observed.lease_generation
            predicates.append(
                f"lease_generation = {marker('lease_generation')}"
            )
            if observed.lease_owner is not None:
                params["lease_owner"] = observed.lease_owner
                predicates.append(f"lease_owner = {marker('lease_owner')}")
        predicates.append(
            _recovery_stale_predicate(
                dbms,
                status=observed.status,
                marker=marker,
            )
        )
        cursor = await database.query(
            f"UPDATE {TaskRun.table_name()} SET {assignments} WHERE "
            + " AND ".join(predicates),
            params,
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def recover_terminal(
        self,
        observed: TaskRun,
        *,
        status: TaskRunStatus,
        error_code: str,
        error: str,
        completed_at: str,
        queue_timeout_seconds: float = 0,
    ) -> TaskRun | None:
        """Terminalize an observed stale snapshot if its CAS fence still holds."""
        if status not in _TERMINAL_STATUSES:
            raise ValueError("recovery status must be terminal")
        if observed.status in _TERMINAL_STATUSES:
            return None
        if queue_timeout_seconds < 0:
            raise ValueError("queue_timeout_seconds cannot be negative")

        sequence = observed.next_event_sequence + 1
        event_data = {"status": status.value, "error_code": error_code}
        envelope = _event_envelope(
            observed.id,
            sequence,
            {"kind": "run_status", "data": event_data},
        )
        patch: dict[str, Any] = {
            "status": status.value,
            "error_code": error_code,
            "error": error,
            "completed_at": completed_at,
            "next_event_sequence": sequence,
            "event_outbox": [*observed.event_outbox, envelope],
            "completion_notification_state": (
                observed.completion_notification_state
                or _terminal_notification_state(observed)
            ),
            "version": observed.version + 1,
        }
        if observed.status in _LEASED_STATUSES:
            patch["lease_generation"] = observed.lease_generation + 1

        changed = await self._recover_terminal_update(
            observed,
            patch=patch,
            queue_timeout_seconds=queue_timeout_seconds,
        )
        if changed != 1:
            return None

        # Terminal ownership release must not depend on event delivery; a
        # failed flush leaves an outbox for startup recovery, not a stuck head.
        await self._release_active(observed.task_id, observed.id)
        await self._flush_outbox_best_effort(observed.id)
        stored = await TaskRun.get(observed.id)
        if stored is None:
            raise RunStateConflict(f"Task run {observed.id} disappeared")
        return stored

    async def _cancel(
        self,
        run_id: str,
        *,
        force: bool,
        reason: str,
        queued_only: bool = False,
        force_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
    ) -> TaskRun | None:
        """CAS a cancellation request and its durable status event together."""
        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None:
                if queued_only:
                    return None
                raise RunStateConflict(f"Task run {run_id} does not exist")

            if run.status in _TERMINAL_STATUSES:
                # A completion/failure racing cancellation is authoritative,
                # but converge any head/outbox work left by that terminal CAS.
                await self._release_active(run.task_id, run.id)
                if run.event_outbox:
                    await self._flush_outbox_best_effort(run_id)
                    run = await TaskRun.get(run_id) or run
                return None if queued_only else run

            if queued_only and run.status != TaskRunStatus.QUEUED:
                return None

            if not force and run.status == TaskRunStatus.CANCELLING:
                if run.event_outbox:
                    await self._flush_outbox_best_effort(run_id)
                    run = await TaskRun.get(run_id) or run
                return run

            if (
                force
                and run.status != TaskRunStatus.QUEUED
                and not _force_cancel_ready(
                    run,
                    now=datetime.now(timezone.utc),
                    grace_seconds=force_grace_seconds,
                )
            ):
                if run.event_outbox:
                    await self._flush_outbox_best_effort(run_id)
                    run = await TaskRun.get(run_id) or run
                return run

            if run.status not in _ACTIVE_STATUSES:
                raise RunStateConflict(
                    f"Task run {run_id} cannot be cancelled from {run.status.value}"
                )

            now = _timestamp()
            terminal = force or run.status == TaskRunStatus.QUEUED
            status = (
                TaskRunStatus.CANCELLED if terminal else TaskRunStatus.CANCELLING
            )
            sequence = run.next_event_sequence + 1
            envelope = _event_envelope(
                run_id,
                sequence,
                {"kind": "run_status", "data": {"status": status.value}},
            )
            patch: dict[str, Any] = {
                "status": status.value,
                "cancel_requested_at": run.cancel_requested_at or now,
                "next_event_sequence": sequence,
                "event_outbox": [*run.event_outbox, envelope],
                "version": run.version + 1,
            }
            if terminal:
                patch.update(
                    error=reason,
                    completed_at=now,
                    completion_notification_state=(
                        run.completion_notification_state
                        or _terminal_notification_state(run)
                    ),
                )
            if force and run.status in _LEASED_STATUSES:
                # Advancing the fencing token guarantees the former worker
                # receives LeaseLost even if it still holds the same owner id.
                patch["lease_generation"] = run.lease_generation + 1

            query: dict[str, Any] = {
                "id": run_id,
                "status": run.status.value,
                "version": run.version,
            }
            if run.status in _LEASED_STATUSES:
                query["lease_generation"] = run.lease_generation
                if run.lease_owner is not None:
                    query["lease_owner"] = run.lease_owner
            changed = await TaskRun.update_one(
                query,
                patch,
            )
            if changed == 1:
                if terminal:
                    # Release before delivery so a transient event-store error
                    # cannot strand a terminal run as the task's active head.
                    await self._release_active(run.task_id, run.id)
                await self._flush_outbox_best_effort(run_id)
                stored = await TaskRun.get(run_id)
                if stored is None:
                    raise RunStateConflict(f"Task run {run_id} disappeared")
                return stored
            await asyncio.sleep(0)
        if queued_only:
            return None
        raise RunStateConflict(f"Task run {run_id} changed too frequently")

    async def request_cancel(
        self,
        run_id: str,
        *,
        reason: str = "cancelled by user",
    ) -> TaskRun:
        """Request cooperative cancellation or cancel an unclaimed run."""
        run = await self._cancel(run_id, force=False, reason=reason)
        assert run is not None
        return run

    async def force_cancel(
        self,
        run_id: str,
        *,
        reason: str = "force-cancelled (worker did not respond)",
        grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
    ) -> TaskRun:
        """Terminalize after grace elapses or the worker lease expires."""
        if grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")
        requested = await self.request_cancel(run_id)
        if requested.status != TaskRunStatus.CANCELLING:
            return requested
        run = await self._cancel(
            run_id,
            force=True,
            reason=reason,
            force_grace_seconds=grace_seconds,
        )
        assert run is not None
        return run

    async def cancel_queued(self, run_id: str) -> TaskRun | None:
        """Compatibility API that only cancels an unclaimed queued run."""
        return await self._cancel(
            run_id,
            force=False,
            reason="cancelled by user",
            queued_only=True,
        )

    async def attach_queue_job_id(self, run_id: str, job_id: str) -> TaskRun:
        """Attach broker metadata without taking over the execution lease.

        A worker can claim immediately after publication, before the API gets
        the broker result. This narrowly scoped CAS writes only the immutable
        job id and version, so it is safe in queued, running, or terminal
        states and cannot overwrite worker-owned lifecycle data.
        """
        if not job_id:
            raise ValueError("job_id is required")
        for _ in range(MAX_CAS_ATTEMPTS):
            run = await TaskRun.get(run_id)
            if run is None:
                raise RunStateConflict(f"Task run {run_id} does not exist")
            if run.queue_job_id == job_id:
                return run
            if run.queue_job_id is not None:
                raise RunStateConflict(
                    f"Task run {run_id} already has a different queue job id"
                )
            changed = await TaskRun.update_one(
                {"id": run_id, "version": run.version},
                {"queue_job_id": job_id, "version": run.version + 1},
            )
            if changed == 1:
                stored = await TaskRun.get(run_id)
                if stored is None:
                    raise RunStateConflict(f"Task run {run_id} disappeared")
                return stored
            await asyncio.sleep(0)
        raise RunStateConflict(f"Task run {run_id} changed too frequently")
