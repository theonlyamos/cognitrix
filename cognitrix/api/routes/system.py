"""Engine status and the cross-run inbox, used by the desktop app's Settings
and Inbox screens (the web UI can use them too)."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends

from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.session_ownership import principal_key

system_api = APIRouter(dependencies=[Depends(jwt_only)])


@system_api.get('/system/info')
async def system_info(_ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """What the local engine is running on. Signed-in sessions only: it names
    folders on this machine."""
    from cognitrix import celery_worker
    from cognitrix.config import settings

    return {
        'version': settings.version,
        'desktop': os.environ.get('COGNITRIX_DESKTOP') == '1',
        'data_folder': str(settings.workdir),
        'working_folder': str(settings.tools_root),
        'database': settings.db_type,
        'task_queue': 'filesystem' if celery_worker.USE_FILESYSTEM_BROKER else 'redis',
        'worker_running': celery_worker.local_worker_running()
        if celery_worker.USE_FILESYSTEM_BROKER else None,
        'worker_log': str(celery_worker.WORKER_LOGFILE)
        if celery_worker.USE_FILESYSTEM_BROKER else None,
    }


def _pending_questions(user_key: str) -> list[dict[str, Any]]:
    from cognitrix.questions import broker

    items = []
    for pending in list(broker._PENDING.values()):
        if str(pending.context.user_key) != user_key or pending.future.done():
            continue
        event = pending.event()
        items.append({
            'kind': 'question',
            'id': pending.request_id,
            'session_id': pending.context.session_id,
            'stream_id': pending.context.stream_id,
            'question': event,
            'created_at': None,
        })
    return items


@system_api.get('/inbox')
async def inbox(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """Everything waiting on the signed-in person: approvals parked by task
    runs (answered with POST /tasks/{task_id}/runs/{run_id}/approvals/{id})
    and questions from chats that are still open."""
    from cognitrix.tasks import approvals
    from cognitrix.tasks.base import Task

    user_key = principal_key(ctx.user)
    rows = await approvals.TaskApproval.find({'user_id': user_key, 'status': approvals.PENDING}) or []
    titles: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for row in rows:
        task_id = row.task_id or ''
        if task_id and task_id not in titles:
            task = await Task.get(task_id)
            titles[task_id] = getattr(task, 'title', '') or ''
        items.append({
            'kind': 'approval',
            **approvals.approval_payload(row),
            'task_title': titles.get(task_id, ''),
        })
    from cognitrix.tasks.automation import public_notification, unread_notifications
    try:
        notes = await unread_notifications(user_key)
    except Exception as exc:
        if 'no such table' not in str(exc).lower():
            raise
        notes = []
    for row in notes:
        task_id = row.task_id or ''
        if task_id and task_id not in titles:
            task = await Task.get(task_id)
            titles[task_id] = getattr(task, 'title', '') or ''
        items.append({
            'kind': 'notification',
            **public_notification(row),
            'task_title': titles.get(task_id, ''),
        })
    items.sort(key=lambda item: item.get('created_at') or '', reverse=True)
    items.extend(_pending_questions(user_key))
    return {'items': items, 'count': len(items)}
