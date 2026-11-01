"""Tools for always-on agents: notes, self-scheduled checks and owner alerts.

Task runs get these automatically (see ``tasks/orchestrator.py``). Each one
only touches the task the current run belongs to, so they are safe under the
least-privilege authority that scheduled and webhook runs carry.
"""
from __future__ import annotations

from datetime import timedelta

from cognitrix.tasks.automation import (
    FMT,
    MESSAGE_MAX_CHARS,
    NOTES_MAX_CHARS,
    REASON_MAX_CHARS,
    TITLE_MAX_CHARS,
    WAKE_MAX_MINUTES,
    WAKE_MIN_MINUTES,
    AgentNotification,
    create_notification,
    utcnow,
)
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import EntityRef, ToolOutcome, current_execution_context

AUTOMATION_TOOL_CATEGORY = 'automation'
MAX_NOTIFICATIONS_PER_RUN = 5


class _NotInTask(Exception):
    pass


async def _current_task():
    """The live task and run this tool call belongs to."""
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    ctx = current_execution_context()
    if not ctx.task_id or not ctx.run_id:
        raise _NotInTask('This tool only works inside a task run.')
    task = await Task.get(ctx.task_id)
    run = await TaskRun.get(ctx.run_id)
    if task is None or task.deleted_at or run is None or str(run.task_id) != str(task.id):
        raise _NotInTask('The task for this run no longer exists.')
    return task, run


def _not_in_task(exc: _NotInTask) -> ToolOutcome:
    return ToolOutcome.failure('not_in_task', str(exc))


@tool(category=AUTOMATION_TOOL_CATEGORY, retryable=False, max_attempts=1,
      approval_mode='assigned_only')
async def read_notes() -> ToolOutcome:
    """Read this task's notes: what earlier runs (and the task owner) wrote down to remember between runs."""
    try:
        task, _run = await _current_task()
    except _NotInTask as exc:
        return _not_in_task(exc)
    notes = (task.notes or '').strip()
    return ToolOutcome.success(notes or '(no notes yet)')


@tool(category=AUTOMATION_TOOL_CATEGORY, retryable=False, max_attempts=1,
      approval_mode='assigned_only')
async def update_notes(content: str, mode: str = 'replace') -> ToolOutcome:
    """Save notes for the next run of this task, such as the last value you saw or what to compare against.
    mode is "replace" (overwrite all notes) or "append" (add a line at the end). Notes hold at most 8000 characters."""
    from cognitrix.tasks.base import Task

    if mode not in ('replace', 'append'):
        return ToolOutcome.failure('invalid_mode', 'mode must be "replace" or "append"')
    try:
        task, _run = await _current_task()
    except _NotInTask as exc:
        return _not_in_task(exc)
    content = str(content or '').strip()
    if mode == 'append':
        existing = (task.notes or '').rstrip()
        content = f'{existing}\n{content}'.strip() if existing else content
    if len(content) > NOTES_MAX_CHARS:
        return ToolOutcome.failure(
            'notes_too_long',
            f'Notes would be {len(content)} characters; the limit is {NOTES_MAX_CHARS}. '
            'Summarize them and save with mode "replace".')
    await Task.update_one({'id': task.id}, {'notes': content or None})
    return ToolOutcome.success(
        f'Notes saved ({len(content)} characters).',
        entities=[EntityRef(type='task', id=str(task.id), name=task.title)])


@tool(category=AUTOMATION_TOOL_CATEGORY, retryable=False, max_attempts=1,
      approval_mode='assigned_only')
async def schedule_next_check(minutes: int, reason: str) -> ToolOutcome:
    """Run this task again after the given number of minutes (1 to 43200, which is 30 days).
    The reason is shown to that run, so say what to look at. Calling it again replaces the earlier request."""
    from cognitrix.tasks.base import Task

    if isinstance(minutes, bool) or not isinstance(minutes, int):
        return ToolOutcome.failure('invalid_minutes', 'minutes must be a whole number')
    if not WAKE_MIN_MINUTES <= minutes <= WAKE_MAX_MINUTES:
        return ToolOutcome.failure(
            'invalid_minutes', f'minutes must be between {WAKE_MIN_MINUTES} and {WAKE_MAX_MINUTES}')
    reason = str(reason or '').strip()[:REASON_MAX_CHARS]
    if not reason:
        return ToolOutcome.failure('reason_required', 'Say why the next check is needed')
    try:
        task, run = await _current_task()
    except _NotInTask as exc:
        return _not_in_task(exc)
    wake_at = (utcnow() + timedelta(minutes=minutes)).strftime(FMT)
    await Task.update_one({'id': task.id}, {
        'wake_pending': True,
        'wake_at': wake_at,
        'wake_reason': reason,
        'wake_requested_by': run.requested_by,
        'wake_authority_kind': run.authority_kind,
        'wake_authority_id': run.authority_id,
    })
    warnings = []
    if task.schedule_enabled and task.next_run_at and task.next_run_at < wake_at:
        warnings.append(
            f"The task's regular schedule runs sooner, at {task.next_run_at} UTC.")
    return ToolOutcome.success(
        f'Next check scheduled for {wake_at} UTC ({minutes} minutes from now).',
        warnings=warnings,
        entities=[EntityRef(type='task', id=str(task.id), name=task.title)])


@tool(category=AUTOMATION_TOOL_CATEGORY, retryable=False, max_attempts=1,
      approval_mode='assigned_only')
async def notify_me(title: str, message: str) -> ToolOutcome:
    """Alert the person you work for: it shows in their Inbox and as a desktop notification.
    Use it only for something that needs their attention, not for routine progress."""
    title = str(title or '').strip()[:TITLE_MAX_CHARS]
    message = str(message or '').strip()[:MESSAGE_MAX_CHARS]
    if not title:
        return ToolOutcome.failure('title_required', 'A notification needs a title')
    ctx = current_execution_context()
    if ctx.task_id and ctx.run_id:
        try:
            task, run = await _current_task()
        except _NotInTask as exc:
            return _not_in_task(exc)
        user_id = task.owner_id or run.requested_by
        if not user_id:
            return ToolOutcome.failure('no_owner', 'This task has no owner to notify')
        sent = await AgentNotification.find({'run_id': str(run.id)}) or []
        if len(sent) >= MAX_NOTIFICATIONS_PER_RUN:
            return ToolOutcome.failure(
                'too_many_notifications',
                f'A run can send at most {MAX_NOTIFICATIONS_PER_RUN} notifications')
        await create_notification(user_id, title, message, task_id=str(task.id), run_id=str(run.id))
    elif ctx.user_id:
        await create_notification(ctx.user_id, title, message, session_id=ctx.session_id)
    else:
        return ToolOutcome.failure('no_owner', 'There is nobody signed in to notify')
    return ToolOutcome.success('Notification sent.')
