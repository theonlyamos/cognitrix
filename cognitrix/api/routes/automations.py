"""Always-on agent endpoints: notes, webhook triggers, wake-ups, the
Automations list, and agent notifications. The public webhook receiver is
``hooks_api``; everything else needs a signed-in session."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from cognitrix.agents.access import can_view
from cognitrix.api.routes.tasks import (
    _require_task_edit,
    _require_task_view,
    _run_authority,
    _run_identity,
    _user_key,
    _visible_task,
)
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.config import API_VERSION
from cognitrix.tasks import Task
from cognitrix.tasks.automation import (
    DISPATCHED,
    EVENT_MAX_BYTES,
    MAX_PENDING_EVENTS,
    MAX_TRIGGERS_PER_TASK,
    NOTES_MAX_CHARS,
    PENDING,
    WAKE_FIELDS,
    AgentNotification,
    TaskTrigger,
    TriggerEvent,
    dispatch_trigger_events,
    new_trigger_token,
    public_notification,
    public_trigger,
    token_matches,
    unread_notifications,
    utcnow_str,
)
from cognitrix.tasks.repository import RunRepository

logger = logging.getLogger('cognitrix.log')

automations_api = APIRouter(dependencies=[Depends(jwt_only)])
hooks_api = APIRouter()

HOOK_PATH = f'/api/{API_VERSION}/hooks/{{trigger_id}}/{{token}}'


# --- notes and wake-ups ---------------------------------------------------------

class NotesBody(BaseModel):
    notes: str | None = None


@automations_api.put('/tasks/{task_id}/notes')
async def save_notes(task_id: str, body: NotesBody, ctx: AuthContext = Depends(get_auth_context)):
    task = await _require_task_edit(await _visible_task(task_id), ctx)
    notes = (body.notes or '').strip()
    if len(notes) > NOTES_MAX_CHARS:
        raise HTTPException(status_code=422, detail=f'Notes must be at most {NOTES_MAX_CHARS} characters')
    await Task.update_one({'id': task.id}, {'notes': notes or None})
    return {'notes': notes or None}


@automations_api.delete('/tasks/{task_id}/wake')
async def cancel_wake(task_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Cancel a pending agent wake-up (Schedule Next Check)."""
    task = await _require_task_edit(await _visible_task(task_id), ctx)
    cleared: dict[str, Any] = {field: None for field in WAKE_FIELDS}
    cleared['wake_pending'] = False
    await Task.update_one({'id': task.id}, cleared)
    return {'wake_pending': False}


# --- triggers -----------------------------------------------------------------------

class TriggerCreate(BaseModel):
    name: str = 'Webhook'


class TriggerPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None


async def _owned_trigger(task_id: str, trigger_id: str, ctx: AuthContext) -> tuple[Task, TaskTrigger]:
    task = await _require_task_edit(await _visible_task(task_id), ctx)
    trigger = await TaskTrigger.get(trigger_id)
    if trigger is None or str(trigger.task_id) != str(task.id):
        raise HTTPException(status_code=404, detail='Trigger not found')
    return task, trigger


def _secret_response(trigger: TaskTrigger, token: str) -> dict[str, Any]:
    return {
        **public_trigger(trigger),
        'token': token,
        'path': HOOK_PATH.format(trigger_id=trigger.id, token=token),
    }


@automations_api.get('/tasks/{task_id}/triggers')
async def list_triggers(task_id: str, ctx: AuthContext = Depends(get_auth_context)):
    task = _require_task_view(await _visible_task(task_id), ctx)
    rows = await TaskTrigger.find({'task_id': task.id}) or []
    rows.sort(key=lambda row: str(row.json().get('created_at') or ''))
    return [public_trigger(row) for row in rows]


@automations_api.post('/tasks/{task_id}/triggers', status_code=201)
async def create_trigger(task_id: str, body: TriggerCreate, ctx: AuthContext = Depends(get_auth_context)):
    """Create a webhook trigger. The secret URL is returned once, here."""
    task = await _require_task_edit(await _visible_task(task_id), ctx)
    if not task.assigned_agents:
        raise HTTPException(status_code=422, detail='Assign an agent or team to the task first')
    existing = await TaskTrigger.find({'task_id': task.id}) or []
    if len(existing) >= MAX_TRIGGERS_PER_TASK:
        raise HTTPException(status_code=422, detail=f'A task can have at most {MAX_TRIGGERS_PER_TASK} triggers')
    token, token_hash = new_trigger_token()
    requested_by, _actor = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    trigger = TaskTrigger(
        task_id=str(task.id),
        owner_id=_user_key(ctx),
        name=(body.name or 'Webhook').strip()[:80] or 'Webhook',
        token_hash=token_hash,
        token_hint=token[-4:],
        requested_by=requested_by,
        authority_kind=authority_kind,
        authority_id=authority_id,
    )
    await trigger.save()
    return _secret_response(trigger, token)


@automations_api.post('/tasks/{task_id}/triggers/{trigger_id}/rotate')
async def rotate_trigger(task_id: str, trigger_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Replace the trigger's secret; the old URL stops working at once."""
    _task, trigger = await _owned_trigger(task_id, trigger_id, ctx)
    token, token_hash = new_trigger_token()
    await TaskTrigger.update_one({'id': trigger.id}, {'token_hash': token_hash, 'token_hint': token[-4:]})
    trigger.token_hash, trigger.token_hint = token_hash, token[-4:]
    return _secret_response(trigger, token)


@automations_api.patch('/tasks/{task_id}/triggers/{trigger_id}')
async def update_trigger(task_id: str, trigger_id: str, body: TriggerPatch,
                         ctx: AuthContext = Depends(get_auth_context)):
    _task, trigger = await _owned_trigger(task_id, trigger_id, ctx)
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates['name'] = body.name.strip()[:80] or 'Webhook'
    if body.enabled is not None:
        updates['enabled'] = body.enabled
        if body.enabled:
            # Re-enabling hands the trigger the current owner's authority.
            requested_by, _actor = _run_identity(ctx)
            authority_kind, authority_id = _run_authority(ctx)
            updates.update(requested_by=requested_by, authority_kind=authority_kind,
                           authority_id=authority_id)
    if updates:
        await TaskTrigger.update_one({'id': trigger.id}, dict(updates))
        for key, value in updates.items():
            setattr(trigger, key, value)
    return public_trigger(trigger)


@automations_api.delete('/tasks/{task_id}/triggers/{trigger_id}')
async def delete_trigger(task_id: str, trigger_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _task, trigger = await _owned_trigger(task_id, trigger_id, ctx)
    await TaskTrigger.remove({'id': trigger.id})
    for event in await TriggerEvent.find({'trigger_id': trigger.id, 'status': PENDING}) or []:
        await TriggerEvent.update_one({'id': event.id, 'status': PENDING},
                                      {'status': 'dropped', 'error': 'trigger deleted'})
    return {'deleted': True}


# --- public webhook receiver -----------------------------------------------------

async def _dispatch_soon() -> None:
    try:
        await dispatch_trigger_events()
    except Exception:
        logger.exception('Immediate webhook dispatch failed; the scheduler will retry')


@hooks_api.post('/hooks/{trigger_id}/{token}', status_code=202)
async def receive_hook(trigger_id: str, token: str, request: Request, background_tasks: BackgroundTasks):
    """Anyone holding the secret URL may queue one run of the task."""
    trigger = await TaskTrigger.get(trigger_id)
    # One answer for unknown, wrong-secret and disabled, so the URL can't be probed.
    if trigger is None or not token_matches(trigger, token) or not trigger.enabled:
        raise HTTPException(status_code=404, detail='Not Found')
    task = await _visible_task(trigger.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Not Found')

    declared = request.headers.get('content-length')
    if declared and declared.isdigit() and int(declared) > EVENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f'Payload must be at most {EVENT_MAX_BYTES} bytes')
    body = b''
    async for chunk in request.stream():
        body += chunk
        if len(body) > EVENT_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f'Payload must be at most {EVENT_MAX_BYTES} bytes')

    pending = await TriggerEvent.find({'task_id': trigger.task_id, 'status': PENDING}) or []
    if len(pending) >= MAX_PENDING_EVENTS:
        raise HTTPException(status_code=429, detail='Too many events are waiting for this task')

    event = TriggerEvent(
        trigger_id=str(trigger.id),
        task_id=str(trigger.task_id),
        payload=body.decode('utf-8', errors='replace'),
        content_type=(request.headers.get('content-type') or '')[:100],
        received_at=utcnow_str(),
    )
    await event.save()
    background_tasks.add_task(_dispatch_soon)
    return {'event_id': event.id, 'status': 'queued'}


# --- automations list ---------------------------------------------------------------

def _is_automation(task: Task, trigger_count: int) -> bool:
    return bool(task.schedule_enabled or task.wake_pending or task.notes or trigger_count
                or task.schedule_at or task.schedule_interval or task.schedule_cron)


async def _automation_summary(task: Task, triggers: list[TaskTrigger]) -> dict[str, Any]:
    latest = await RunRepository().latest_run(task.id)
    last_run = None
    if latest is not None:
        status = getattr(latest.status, 'value', latest.status)
        last_run = {
            'id': latest.id,
            'status': status,
            'trigger_kind': latest.trigger_kind,
            'completed_at': latest.completed_at,
            'result': (latest.result or '')[:300],
        }
    pending_events = await TriggerEvent.find({'task_id': task.id, 'status': PENDING}) or []
    return {
        'id': task.id,
        'title': task.title,
        'schedule_at': task.schedule_at,
        'schedule_interval': task.schedule_interval,
        'schedule_cron': task.schedule_cron,
        'schedule_enabled': bool(task.schedule_enabled),
        'next_run_at': task.next_run_at,
        'wake_pending': bool(task.wake_pending),
        'wake_at': task.wake_at,
        'wake_reason': task.wake_reason,
        'notes': (task.notes or '')[:300],
        'triggers': [public_trigger(row) for row in triggers],
        'pending_events': len(pending_events),
        'last_run': last_run,
    }


@automations_api.get('/automations')
async def list_automations(ctx: AuthContext = Depends(get_auth_context)):
    user_key = _user_key(ctx)
    by_task: dict[str, list[TaskTrigger]] = {}
    for row in await TaskTrigger.find({}) or []:
        by_task.setdefault(str(row.task_id), []).append(row)
    tasks = [
        task for task in await Task.all() or []
        if not task.deleted_at and can_view(task, user_key)
        and _is_automation(task, len(by_task.get(str(task.id), [])))
    ]
    return await asyncio.gather(*[
        _automation_summary(task, by_task.get(str(task.id), [])) for task in tasks
    ])


@automations_api.get('/tasks/{task_id}/events')
async def list_trigger_events(task_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Recent webhook deliveries for a task, newest first (payloads trimmed)."""
    task = _require_task_view(await _visible_task(task_id), ctx)
    rows = await TriggerEvent.find({'task_id': task.id}) or []
    rows.sort(key=lambda row: row.received_at or '', reverse=True)
    return [{
        'id': row.id,
        'trigger_id': row.trigger_id,
        'status': row.status,
        'received_at': row.received_at,
        'run_id': row.run_id if row.status == DISPATCHED else None,
        'payload': (row.payload or '')[:500],
        'error': row.error,
    } for row in rows[:20]]


# --- notifications ------------------------------------------------------------------

@automations_api.get('/notifications')
async def list_notifications(ctx: AuthContext = Depends(get_auth_context)):
    rows = await unread_notifications(_user_key(ctx))
    rows.sort(key=lambda row: str(row.json().get('created_at') or ''), reverse=True)
    return [public_notification(row) for row in rows]


@automations_api.post('/notifications/{notification_id}/read')
async def read_notification(notification_id: str, ctx: AuthContext = Depends(get_auth_context)):
    row = await AgentNotification.get(notification_id)
    if row is None or str(row.user_id) != str(_user_key(ctx)):
        raise HTTPException(status_code=404, detail='Notification not found')
    await AgentNotification.update_one({'id': row.id}, {'read_at': utcnow_str()})
    return {'read': True}


@automations_api.post('/notifications/read-all')
async def read_all_notifications(ctx: AuthContext = Depends(get_auth_context)):
    rows = await unread_notifications(_user_key(ctx))
    now = utcnow_str()
    for row in rows:
        await AgentNotification.update_one({'id': row.id}, {'read_at': now})
    return {'read': len(rows)}
