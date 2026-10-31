"""Parked approvals for durable task runs.

A task step that hits a risky tool call (for example Write or Edit) used to
be denied outright, because a background worker has nobody to ask. Now the
call is parked: an approval record is stored, an ``approval_requested`` run
event is emitted, and the step waits until the person who started the run
approves or denies it in the web UI (or the wait times out).

Records live in the database rather than in memory, so the API process can
answer an approval for a step running in a separate Celery worker.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from odbms import Model
from pydantic import Field

logger = logging.getLogger('cognitrix.log')

PENDING = 'pending'
APPROVED = 'approved'
DENIED = 'denied'
EXPIRED = 'expired'

# Set by the task step executor around a step's turn. None outside tasks.
task_turn_ctx: ContextVar[dict | None] = ContextVar('task_turn_ctx', default=None)


def approval_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv('COGNITRIX_TASK_APPROVAL_TIMEOUT', '1800')))
    except ValueError:
        return 1800.0


def _poll_interval() -> float:
    try:
        return max(0.05, float(os.getenv('COGNITRIX_TASK_APPROVAL_POLL', '1.0')))
    except ValueError:
        return 1.0


class TaskApproval(Model):
    run_id: str
    task_id: str | None = None
    step_index: int | None = None
    user_id: str
    """The person who may answer: the user who started the run."""
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = ''
    details: str = ''
    op_hash: str = ''
    status: str = PENDING
    decided_at: str | None = None


def operation_hash(tool_name: str, params: dict[str, Any]) -> str:
    payload = json.dumps({'tool': tool_name, 'params': params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def approval_payload(row: TaskApproval) -> dict[str, Any]:
    return {
        'id': row.id,
        'run_id': row.run_id,
        'task_id': row.task_id,
        'step_index': row.step_index,
        'tool_name': row.tool_name,
        'params': row.params,
        'risk_level': row.risk_level,
        'details': row.details,
        'status': row.status,
        'created_at': str(getattr(row, 'created_at', '') or ''),
        'decided_at': row.decided_at,
    }


async def _emit(ctx: dict, kind: str, data: dict[str, Any]) -> None:
    emit = ctx.get('emit')
    if emit is None:
        return
    try:
        await emit(kind, data)
    except Exception:
        logger.exception('Could not emit %s for run %s', kind, ctx.get('run_id'))


async def request_task_approval(ctx: dict, tool_name: str, params: dict[str, Any], risk) -> tuple[bool, str | None]:
    """Park a risky task tool call until its run's owner answers.

    Returns (approved, error). A decision already recorded for the same call
    in this run is reused, so a resumed step does not ask twice.
    """
    run_id = str(ctx['run_id'])
    user_id = str(ctx['user_id'])
    op_hash = operation_hash(tool_name, params)

    for row in await TaskApproval.find({'run_id': run_id, 'op_hash': op_hash}) or []:
        if row.status == APPROVED:
            return True, None
        if row.status == DENIED:
            return False, None

    row = TaskApproval(
        run_id=run_id,
        task_id=str(ctx.get('task_id') or '') or None,
        step_index=ctx.get('step_index'),
        user_id=user_id,
        tool_name=tool_name,
        params=params,
        risk_level=getattr(getattr(risk, 'risk_level', None), 'value', str(getattr(risk, 'risk_level', ''))),
        details=str(getattr(risk, 'details', '') or ''),
        op_hash=op_hash,
    )
    await row.save()
    await _emit(ctx, 'approval_requested', approval_payload(row))

    deadline = asyncio.get_running_loop().time() + approval_timeout_seconds()
    interval = _poll_interval()
    status = PENDING
    try:
        while True:
            fresh = await TaskApproval.get(row.id)
            status = getattr(fresh, 'status', PENDING) if fresh is not None else DENIED
            if status != PENDING:
                break
            if asyncio.get_running_loop().time() >= deadline:
                status = EXPIRED
                await TaskApproval.update_one(
                    {'id': row.id, 'status': PENDING},
                    {'status': EXPIRED, 'decided_at': datetime.utcnow().isoformat()},
                )
                fresh = await TaskApproval.get(row.id)
                status = getattr(fresh, 'status', EXPIRED) if fresh is not None else EXPIRED
                if status == PENDING:
                    status = EXPIRED
                break
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        try:
            await TaskApproval.update_one(
                {'id': row.id, 'status': PENDING},
                {'status': EXPIRED, 'decided_at': datetime.utcnow().isoformat()},
            )
        except Exception:
            pass
        raise

    row.status = status
    await _emit(ctx, 'approval_resolved', approval_payload(row))
    if status == APPROVED:
        return True, None
    if status == EXPIRED:
        return False, f"approval for {tool_name} timed out"
    return False, None


async def decide(approval_id: str, run_id: str, approved: bool) -> TaskApproval | None:
    """Record an answer. Returns the row, or None if it isn't pending."""
    row = await TaskApproval.get(approval_id)
    if row is None or row.run_id != run_id or row.status != PENDING:
        return None
    status = APPROVED if approved else DENIED
    await TaskApproval.update_one(
        {'id': approval_id, 'status': PENDING},
        {'status': status, 'decided_at': datetime.utcnow().isoformat()},
    )
    fresh = await TaskApproval.get(approval_id)
    return fresh if fresh is not None and fresh.status == status else None


async def list_for_run(run_id: str, status: str | None = None) -> list[TaskApproval]:
    query: dict[str, Any] = {'run_id': run_id}
    if status:
        query['status'] = status
    rows = await TaskApproval.find(query) or []
    return sorted(rows, key=lambda r: str(getattr(r, 'created_at', '') or ''))
