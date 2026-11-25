"""Tools for swarms: a lead agent starts many workers and gets one report back.

``Spawn Workers`` and ``Swarm`` are the lead's; ``Swarm Team`` is what a
worker gets. See ``cognitrix/swarms/service.py`` for how a swarm runs.
"""
from __future__ import annotations

import contextvars
from typing import Any

from cognitrix.app_settings import get as app_setting
from cognitrix.swarms import service
from cognitrix.swarms.service import SwarmError
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import EntityRef, ToolOutcome, current_execution_context

SWARM_TOOL_CATEGORY = 'swarm'
SWARM_WORKER_TOOL_CATEGORY = 'swarm_worker'
SWARM_TOOL_CATEGORIES = frozenset({SWARM_TOOL_CATEGORY, SWARM_WORKER_TOOL_CATEGORY})

# Set for a turn the person sent with the Swarm switch on: the lead may start
# any number of workers (inside the limits) without asking first.
swarm_requested: contextvars.ContextVar[bool] = contextvars.ContextVar('swarm_requested', default=False)

SWARM_MODE_NOTE = (
    'Swarm is on for this message: split the job across workers with Spawn Workers '
    '(one worker per independent part, or one per item for a list), wait for the combined report, '
    'check it, then answer with one result. If the job is a single small step, just do it.'
)


def wait_seconds() -> float:
    """How long Swarm (action "wait") holds the lead's turn for a report."""
    try:
        return max(0.0, float(app_setting('COGNITRIX_SWARM_WAIT_SECONDS', '') or 45))
    except ValueError:
        return 45.0


def _failure(exc: SwarmError) -> ToolOutcome:
    return ToolOutcome.failure(exc.code, exc.message)


def _inline(ctx: Any) -> bool:
    return bool(ctx.run_id)


async def _lead_chat(ctx: Any):
    """The chat a lead tool was called from; refuses chats that cannot lead."""
    from cognitrix.sessions.base import Session

    if ctx.restricted:
        raise SwarmError('not_allowed', 'API key calls cannot start swarms.')
    if not ctx.user_id:
        raise SwarmError('no_owner', 'A swarm needs a signed-in person to own it.')
    current = await Session.get(ctx.session_id) if ctx.session_id else None
    if current is None:
        raise SwarmError('no_chat', 'Swarms start from a chat.')
    if getattr(current, 'incognito', False):
        raise SwarmError('incognito', "This is an incognito chat, so it can't start workers whose chats would be saved.")
    if (getattr(current, 'started_by', None) or {}).get('swarm_id'):
        raise SwarmError('too_deep', 'You are a worker in a swarm and cannot start one. Use Swarm Team (action "add_work").')
    if current.group_chat:
        raise SwarmError('not_here', 'Swarms cannot start from a team group chat.')
    return current


async def _confirm(count: int, limits: dict[str, int], title: str) -> None:
    """Ask the person before starting more workers than the agent may start alone."""
    from cognitrix.questions.broker import QuestionChannelUnavailable, ask_question
    from cognitrix.questions.models import QuestionSpec

    spec = QuestionSpec.from_tool_args(
        prompt=f'Start {count} workers for "{title}"?',
        options=[
            {'id': 'start', 'label': f'Start {count} workers',
             'description': f'{limits["concurrency"]} run at once; the rest wait their turn.'},
            {'id': 'no', 'label': "Don't start", 'description': 'The agent carries on without a swarm.'},
        ],
        details=(f'Each worker is a separate model run, so this uses several times the tokens of one chat. '
                 f'The swarm stops starting workers at {limits["token_budget"]:,} tokens.'),
        allow_free_text=False,
        recommended_option_id='start',
        auto_submit_recommended=False,
    )
    try:
        answer = await ask_question(spec)
    except QuestionChannelUnavailable:
        raise SwarmError(
            'needs_confirmation',
            f'Starting {count} workers needs the person\'s OK and they cannot be asked here. '
            f'Start at most {limits["auto_start"]}, or tell them what you would start and wait.') from None
    if getattr(answer, 'option_id', None) != 'start':
        raise SwarmError('declined', 'The person chose not to start the swarm. Carry on without it.')


def _wake(wait: str) -> dict[str, Any]:
    value = str(wait or 'all').strip().lower()
    if value in ('any', 'first', 'each'):
        return {'until': 'count', 'count': 1}
    if value in ('failure', 'first_failure', 'any_failure'):
        return {'until': 'failure'}
    if value.isdigit() and int(value) > 0:
        return {'until': 'count', 'count': int(value)}
    return {'until': 'all'}


@tool(category=SWARM_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'],
      occupies_execution_slot=False)
async def spawn_workers(
    workers: list[dict[str, Any]] | str | None = None,
    title: str = '',
    items: list[Any] | str | None = None,
    brief: str = '',
    role: str = '',
    model: str = '',
    attempts: int = 0,
    models: list[str] | None = None,
    review: bool = False,
    wait: str = 'all',
    *, parent=None,
) -> ToolOutcome:
    """Start a swarm: workers that each do one part of a bigger job at the same time, each in its own chat
    with a fresh context. You get ONE combined report when they finish, not a reply per worker. Use no
    workers for a question or a small job (just do it), 2 to 5 for a job with separate parts, one per item
    for a list of similar items, and more than 10 only for wide research: every worker is a full model run.
    Call it one of three ways: workers=[...] for separate parts; items=[...] with brief="... {item} ..." for
    the same job per item; attempts=3 with brief="..." to give one job to several workers and have a judge
    pick the best. Workers are read-only unless a worker sets may_edit.

    :param workers: Separate parts, a list of objects: brief (required; the worker sees nothing else, so give the goal, the files or sources, what is known, exactly what to send back and what not to do), name, role (a saved agent's name from List Agents, or any made-up role such as "skeptical reviewer" for a copy of you), persona (one line), model ("fast" or "provider/model"), may_edit (true lets it change files), depends_on (names of workers that must finish first; it is given their summaries).
    :param title: A short name for the whole job, shown to the person.
    :param items: The items to fan out over, one worker each. Use with brief.
    :param brief: The job for each item (write {item} where the item goes), or the job every attempt gets.
    :param role: Role for every worker when using items or attempts.
    :param model: Model for every worker when using items or attempts ("fast" or "provider/model").
    :param attempts: Give the same brief to this many workers (2 to 8); a judge picks the best result.
    :param models: One model per attempt, to compare models on the same job.
    :param review: Have a reviewer check each result and send it back once if it falls short.
    :param wait: When to report to you: "all" (default), "any" (after each worker), a number (after every N workers) or "failure" (as soon as one fails, and at the end).
    """
    ctx = current_execution_context()
    inline = _inline(ctx)
    try:
        mode = 'fanout'
        if attempts and int(attempts) > 1:
            if not str(brief or '').strip():
                raise SwarmError('brief_required', 'Best of N needs the brief every attempt gets.')
            mode = 'best_of'
            picks = [str(m) for m in (models or [])]
            raw = [{'brief': brief, 'name': f'attempt-{i + 1}', 'role': role,
                    'model': picks[i] if i < len(picks) else model} for i in range(min(int(attempts), 8))]
        elif items:
            raw = service.fan_out(items, brief, role=role, model=model)
        else:
            raw = workers
        specs = service.parse_specs(raw)
        if parent is None or not getattr(parent, 'id', None):
            raise SwarmError('no_lead', 'A swarm needs the agent that leads it.')

        if inline:
            user_key = str(ctx.user_id or '')
            swarm = await service.start_swarm(
                user_key, parent, specs, title=title, mode=mode, review=bool(review), inline=True, ctx=ctx,
                task_id=str(ctx.task_id or ''), run_id=str(ctx.run_id or ''),
                step_index=await _step_index(ctx), sandbox=bool(ctx.sandbox))
            return ToolOutcome.success(
                await _inline_report(user_key, str(swarm.id)),
                entities=[EntityRef(type='swarm', id=str(swarm.id), name=swarm.title)])

        current = await _lead_chat(ctx)
        user_key = str(ctx.user_id)
        settings = await service.get_settings(user_key)
        if not settings.enabled:
            raise SwarmError('swarms_off', 'Swarms are turned off in Settings. Do the work yourself or with Start Chat.')
        limits = service.effective_limits(settings)
        if len(specs) > limits['max_workers']:
            raise SwarmError('too_many_workers',
                             f'A swarm holds at most {limits["max_workers"]} workers; you asked for {len(specs)}. '
                             'Group the work into fewer parts.')
        name = (title or '').strip()[:80] or 'Swarm'
        if len(specs) > limits['auto_start'] and not swarm_requested.get():
            await _confirm(len(specs), limits, name)
        from cognitrix.tools.chats import _shared_folders

        swarm = await service.start_swarm(
            user_key, parent, specs, session_id=str(current.id), title=name, mode=mode, review=bool(review),
            wake=_wake(wait), folders=_shared_folders(current, ctx), sandbox=bool(ctx.sandbox))
    except SwarmError as exc:
        return _failure(exc)

    rows = await service.workers_of(str(swarm.id))
    names = ', '.join(w.name for w in rows[:12]) + (f' and {len(rows) - 12} more' if len(rows) > 12 else '')
    at_once = int((swarm.limits or {}).get('concurrency') or 0)
    return ToolOutcome.success(
        f'Started the swarm "{swarm.title}" (swarm id {swarm.id}) with {len(rows)} worker(s): {names}. '
        f'Up to {at_once} run at the same time. One combined report will be sent to you here when they are '
        'done. Tell the person in a line or two what is running, then end your turn, or call Swarm '
        '(action "wait") to hold on briefly for a quick job. Do not do the workers\' jobs yourself meanwhile.',
        entities=[EntityRef(type='swarm', id=str(swarm.id), name=swarm.title)])


async def _step_index(ctx: Any) -> int:
    """The task-run step this turn is part of, so a retried step finds its earlier swarm."""
    try:
        from cognitrix.tasks.approvals import task_turn_ctx

        turn = task_turn_ctx.get()
        value = (turn or {}).get('step_index') if isinstance(turn, dict) else getattr(turn, 'step_index', None)
        return int(value) if value is not None else -1
    except Exception:
        return -1


async def _inline_report(user_key: str, swarm_id: str) -> str:
    """Wait for a swarm inside a task run and return its whole report."""
    parts: list[str] = []
    waited = 0.0
    while waited < service.INLINE_WAIT_SECONDS:
        text = await service.wait_for_report(user_key, swarm_id, 30.0)
        waited += 30.0 if not text else 0.0
        if text:
            parts.append(text)
        swarm = await service.get_swarm(user_key, swarm_id)
        if swarm is None or swarm.status != 'running':
            break
    else:
        await service.stop_swarm(user_key, swarm_id, reason='Took too long for one task step.')
        parts.append('The swarm was stopped: it took too long for one task step.')
    tail = await service.wait_for_report(user_key, swarm_id, 0.0)
    if tail:
        parts.append(tail)
    return '\n\n'.join(parts) or 'The swarm ended without results.'


async def _my_swarm(ctx: Any, swarm_id: str):
    """The swarm a lead tool call is about: the one named, else this chat's newest."""
    user_key = str(ctx.user_id or '')
    if swarm_id:
        swarm = await service.get_swarm(user_key, str(swarm_id).strip())
    elif ctx.session_id and not _inline(ctx):
        swarm = await service.latest_for_chat(user_key, str(ctx.session_id))
    else:
        swarm = None
    owned = swarm is not None and (
        (ctx.run_id and swarm.run_id == str(ctx.run_id))
        or (not ctx.run_id and ctx.session_id and swarm.session_id == str(ctx.session_id)))
    if not owned:
        raise SwarmError('swarm_not_found', 'No swarm that this chat started. Start one with Spawn Workers.')
    return user_key, swarm


@tool(category=SWARM_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'],
      occupies_execution_slot=False)
async def swarm(action: str, worker: str = '', message: str = '', until: str = '', file: str = '',
                offset: int = 0, swarm_id: str = '', *, parent=None) -> ToolOutcome:
    """Look at and steer a swarm you started with Spawn Workers. Actions: "status" shows the board (every
    worker, its state and what it is doing); "wait" holds on briefly for the next report; "result" reads one
    worker's full result; "message" sends a worker a correction or follow-up (a finished worker goes back to
    work and its new result is reported); "stop" stops one worker, or the whole swarm when no worker is
    named; "merge" brings the file changes of a worker that edited its own copy of the project into the project.

    :param action: status, wait, result, message, stop or merge.
    :param worker: The worker's name, for result, message, merge and stopping one worker.
    :param message: What to tell the worker (action "message").
    :param until: With "wait", change when you are told: "all", "any", a number or "failure".
    :param file: With "result", another file the worker left instead of its result.
    :param offset: With "result", where to carry on reading a long result.
    :param swarm_id: The swarm, when this chat started more than one. Default: the newest.
    """
    ctx = current_execution_context()
    verb = str(action or '').strip().lower()
    try:
        user_key, run = await _my_swarm(ctx, swarm_id)
        sid = str(run.id)
        if verb in ('status', 'board', 'list'):
            return ToolOutcome.success(service.board_text(run, await service.workers_of(sid)))
        if verb == 'wait':
            if until:
                await service.set_wake(user_key, sid, _wake(until))
            text = await service.wait_for_report(user_key, sid, wait_seconds())
            if text:
                return ToolOutcome.success(text)
            run = await service.get_swarm(user_key, sid) or run
            state = service.ledger(run, await service.workers_of(sid))
            if state['finished']:
                return ToolOutcome.success('Nothing new: you already have every worker\'s report. '
                                           'Use action "result" for full results.')
            return ToolOutcome.success(
                f'Still working ({state["done"]} of {state["total"]} done, {state["running"]} running, '
                f'{state["queued"]} waiting). The report will be sent to you here when it is ready. '
                'Tell the person what is running and end your turn; do not keep waiting.')
        if verb == 'result':
            return ToolOutcome.success(await service.read_result(user_key, sid, worker, file=file, offset=offset))
        if verb == 'message':
            return ToolOutcome.success(await service.send_message(user_key, sid, worker, message))
        if verb == 'stop':
            if str(worker or '').strip():
                row = await service.stop_worker(user_key, sid, worker)
                return ToolOutcome.success(f'Stopped "{row.name}".')
            await service.stop_swarm(user_key, sid, reason=f'Stopped by {getattr(parent, "name", "the lead")}.')
            return ToolOutcome.success(f'Stopped the swarm "{run.title}".')
        if verb == 'merge':
            ok, text = await service.merge_worker(user_key, sid, worker)
            return ToolOutcome.success(text) if ok else ToolOutcome.failure('merge_failed', text)
        raise SwarmError('bad_action', 'action must be status, wait, result, message, stop or merge.')
    except SwarmError as exc:
        return _failure(exc)


async def _me(ctx: Any) -> tuple[str, str, str]:
    """(person, swarm id, worker name) for a worker's own tool call."""
    from cognitrix.sessions.base import Session

    current = await Session.get(ctx.session_id) if ctx.session_id else None
    started_by = (getattr(current, 'started_by', None) or {}) if current is not None else {}
    if not ctx.user_id or not started_by.get('swarm_id') or not started_by.get('worker'):
        raise SwarmError('not_a_worker', 'Swarm Team is for workers of a swarm.')
    return str(ctx.user_id), str(started_by['swarm_id']), str(started_by['worker'])


@tool(category=SWARM_WORKER_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def swarm_team(action: str, worker: str = '', message: str = '', brief: str = '', name: str = '',
                     role: str = '', *, parent=None) -> ToolOutcome:
    """Work with the other workers of the swarm you are part of. Actions: "board" shows who is in the
    swarm, their jobs and how far they are; "result" reads the full result of a teammate that has finished;
    "message" tells a teammate, or the lead (worker "lead"), something they need for their job, such as a
    finding that changes their work or a clash (not thanks or status); "add_work" adds a separate job you
    found for another worker to do, while you carry on with your own.

    :param action: board, result, message or add_work.
    :param worker: The teammate's name, or "lead" for the lead.
    :param message: What to tell them (action "message").
    :param brief: The job to add (action "add_work"): everything its worker needs to know.
    :param name: A short name for the added job.
    :param role: Who should do the added job: a saved agent's name or a made-up role.
    """
    ctx = current_execution_context()
    verb = str(action or '').strip().lower()
    try:
        user_key, sid, me = await _me(ctx)
        run = await service.get_swarm(user_key, sid)
        if run is None:
            raise SwarmError('swarm_not_found', 'Your swarm no longer exists.')
        if verb in ('board', 'status', 'list'):
            return ToolOutcome.success(service.board_text(run, await service.workers_of(sid), me=me))
        if verb == 'result':
            return ToolOutcome.success(await service.read_result(user_key, sid, worker))
        if verb == 'message':
            return ToolOutcome.success(await service.send_message(user_key, sid, worker, message, sender=me))
        if verb in ('add_work', 'add'):
            specs = service.parse_specs([{'brief': brief, 'name': name, 'role': role}])
            rows = await service.add_workers(user_key, sid, specs, added_by=me)
            return ToolOutcome.success(
                f'Added "{rows[0].name}" to the board. Another worker will do it; carry on with your own job.')
        raise SwarmError('bad_action', 'action must be board, result, message or add_work.')
    except SwarmError as exc:
        return _failure(exc)
