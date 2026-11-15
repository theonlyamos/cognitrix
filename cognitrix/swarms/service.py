"""How a swarm runs.

A lead agent starts a swarm from its chat (Spawn Workers). Each worker is a
job with a brief, run by a saved agent or by a copy of the lead in a made-up
role, in a chat of its own with a fresh context. Workers wait their turn
behind a concurrency limit, and behind the workers they depend on. When a
worker ends, its reply is saved to a file in the swarm's folder and a short
summary is kept. The lead is woken once with one combined report, not once
per worker.

Inside a task run (``inline``), workers are copies of the step's agent run in
the task's own process, and Spawn Workers waits for them.

Limits: workers per swarm, workers running at once (per swarm and per
person), tokens per swarm, one level deep (a worker cannot start a swarm).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cognitrix.swarms import worktrees
from cognitrix.swarms.models import (
    ENDED,
    SwarmRun,
    SwarmSettings,
    SwarmWorker,
    ceiling_concurrency,
    ceiling_tokens,
    ceiling_workers,
)

logger = logging.getLogger('cognitrix.log')

BRIEF_MAX_CHARS = 8000
SUMMARY_MAX_CHARS = 900
RESULT_READ_CHARS = 24_000
NAME_MAX = 40
# Messages a worker may be sent after it started (lead and teammates together),
# and how many one teammate may send another.
MAX_MESSAGES_PER_WORKER = 6
MAX_MESSAGES_PER_PAIR = 3
# Jobs one worker may add to the board (Add Work).
MAX_ADDED_PER_WORKER = 3
MAX_COPIED_FILES = 20
MAX_COPIED_BYTES = 20 * 1024 * 1024
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_DELAYS = (20.0, 60.0)
WATCH_SECONDS = 20.0
# Spawn Workers inside a task run waits at most this long for its workers.
INLINE_WAIT_SECONDS = 1500.0

_RATE_LIMIT = re.compile(r'\b(429|rate.?limit|overloaded|too many requests|quota)\b', re.IGNORECASE)


def worker_timeout() -> float:
    """How long one worker turn may take."""
    try:
        return max(60.0, float(os.environ.get('COGNITRIX_SWARM_WORKER_TIMEOUT', '') or 900))
    except ValueError:
        return 900.0


def stall_seconds() -> float:
    """A running worker with no new output for this long is called stalled."""
    try:
        return max(30.0, float(os.environ.get('COGNITRIX_SWARM_STALL_SECONDS', '') or 300))
    except ValueError:
        return 300.0


class SwarmError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


# ---------------------------------------------------------------------------
# Settings and limits
# ---------------------------------------------------------------------------

async def get_settings(user_key: str) -> SwarmSettings:
    row = await SwarmSettings.find_one({'user_id': str(user_key)})
    return row or SwarmSettings(user_id=str(user_key))


def effective_limits(settings: SwarmSettings) -> dict[str, int]:
    """The person's own settings, held inside the admin's and the server's ceilings."""
    person_cap = min(ceiling_concurrency(), settings.admin_concurrency or ceiling_concurrency())
    worker_cap = min(ceiling_workers(), settings.admin_max_workers or ceiling_workers())
    return {
        'concurrency': max(1, min(settings.concurrency, person_cap)),
        'per_person': max(1, person_cap),
        'max_workers': max(1, min(settings.max_workers, worker_cap)),
        'token_budget': max(10_000, min(settings.token_budget, ceiling_tokens())),
        'auto_start': max(0, settings.auto_start),
    }


def public_settings(settings: SwarmSettings) -> dict[str, Any]:
    limits = effective_limits(settings)
    return {
        'enabled': bool(settings.enabled),
        'concurrency': limits['concurrency'],
        'max_workers': limits['max_workers'],
        'token_budget': limits['token_budget'],
        'auto_start': limits['auto_start'],
        'ceilings': {
            'concurrency': limits['per_person'],
            'max_workers': min(ceiling_workers(), settings.admin_max_workers or ceiling_workers()),
            'token_budget': ceiling_tokens(),
        },
    }


async def save_settings(user_key: str, changes: dict[str, Any], *, admin: bool = False) -> SwarmSettings:
    allowed = {'enabled', 'concurrency', 'max_workers', 'token_budget', 'auto_start'}
    if admin:
        allowed = {'admin_concurrency', 'admin_max_workers'}
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in allowed or value is None:
            continue
        if key == 'enabled':
            clean[key] = bool(value)
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SwarmError('bad_setting', f'{key} must be a whole number') from None
        low = 0 if key in ('auto_start', 'admin_concurrency', 'admin_max_workers') else 1
        if number < low or number > 100_000_000:
            raise SwarmError('bad_setting', f'{key} is out of range')
        clean[key] = number
    row = await SwarmSettings.find_one({'user_id': str(user_key)})
    if row is None:
        row = SwarmSettings(user_id=str(user_key), **clean)
        await row.save()
        return row
    if clean:
        await SwarmSettings.update_one({'id': row.id}, clean)
        for key, value in clean.items():
            setattr(row, key, value)
    return row


# ---------------------------------------------------------------------------
# What the lead asks for
# ---------------------------------------------------------------------------

@dataclass
class WorkerSpec:
    brief: str
    name: str = ''
    role: str = ''
    persona: str = ''
    model: str = ''
    may_edit: bool = False
    depends_on: list[str] = field(default_factory=list)


def slug(text: str, fallback: str = 'worker') -> str:
    text = re.sub(r'[^a-z0-9]+', '-', str(text or '').strip().lower()).strip('-')
    return text[:NAME_MAX].strip('-') or fallback


def parse_specs(workers: Any) -> list[WorkerSpec]:
    """Turn the tool's ``workers`` argument into checked specs."""
    if isinstance(workers, str):
        import json

        try:
            workers = json.loads(workers)
        except ValueError:
            raise SwarmError('bad_workers', 'workers must be a list of {"brief": ...} objects') from None
    if isinstance(workers, dict):
        workers = [workers]
    if not isinstance(workers, list) or not workers:
        raise SwarmError('bad_workers', 'Give at least one worker: [{"brief": "..."}]')
    specs: list[WorkerSpec] = []
    for item in workers:
        if isinstance(item, str):
            item = {'brief': item}
        if not isinstance(item, dict):
            raise SwarmError('bad_workers', 'Each worker is an object with at least a "brief"')
        brief = str(item.get('brief') or item.get('task') or '').strip()
        if not brief:
            raise SwarmError('brief_required', 'Every worker needs a brief saying what to do')
        if len(brief) > BRIEF_MAX_CHARS:
            raise SwarmError('brief_too_long', f'A brief holds at most {BRIEF_MAX_CHARS} characters')
        deps = item.get('depends_on') or item.get('after') or []
        if isinstance(deps, str):
            deps = [deps]
        specs.append(WorkerSpec(
            brief=brief,
            name=str(item.get('name') or '').strip(),
            role=str(item.get('role') or item.get('agent') or '').strip(),
            persona=str(item.get('persona') or '').strip()[:600],
            model=str(item.get('model') or '').strip(),
            may_edit=bool(item.get('may_edit') or item.get('edit')),
            depends_on=[slug(str(d), '') for d in deps if str(d).strip()],
        ))
    return specs


def fan_out(items: Any, template: str, *, role: str = '', model: str = '', may_edit: bool = False) -> list[dict[str, Any]]:
    """One worker per item: ``template`` with {item} replaced."""
    if isinstance(items, str):
        import json

        try:
            items = json.loads(items)
        except ValueError:
            items = [line.strip() for line in items.splitlines() if line.strip()]
    if not isinstance(items, list) or not items:
        raise SwarmError('bad_items', 'items must be a list')
    template = str(template or '').strip()
    if not template:
        raise SwarmError('brief_required', 'Fan-out needs a brief that says what to do with each item')
    out = []
    for item in items:
        text = item if isinstance(item, str) else _json(item)
        brief = template.replace('{item}', text) if '{item}' in template else f'{template}\n\nItem: {text}'
        out.append({'brief': brief, 'name': slug(text[:NAME_MAX], ''), 'role': role, 'model': model,
                    'may_edit': may_edit})
    return out


def _json(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def parse_model(value: str, llm: Any) -> dict[str, str]:
    """'' (the agent's own), 'fast', 'provider/model' or a bare model name of the agent's provider."""
    value = str(value or '').strip()
    if not value or value.lower() in ('same', 'default', 'inherit'):
        return {}
    provider = str(getattr(llm, 'provider', '') or '').lower()
    if value.lower() == 'fast':
        from cognitrix.agents.starter_team import FAST_MODELS

        fast = FAST_MODELS.get(provider)
        return {'provider': provider, 'model': fast} if fast else {}
    from cognitrix.providers.base import CHAT_MODEL_PROVIDERS

    head, _, tail = value.partition('/')
    if tail and head.lower() in CHAT_MODEL_PROVIDERS:
        return {'provider': head.lower(), 'model': tail}
    return {'provider': provider, 'model': value}


# ---------------------------------------------------------------------------
# Live state (this process)
# ---------------------------------------------------------------------------

@dataclass
class _Live:
    swarm_id: str
    user_key: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    # Chat runs (or inline tasks) of running workers, by worker name.
    runs: dict[str, Any] = field(default_factory=dict)
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    spent: dict[str, int] = field(default_factory=dict)
    activity: dict[str, tuple[int, float]] = field(default_factory=dict)
    pairs: dict[tuple[str, str], int] = field(default_factory=dict)
    added: dict[str, int] = field(default_factory=dict)
    over_budget: bool = False
    inline: bool = False
    # Lead tool calls holding on for the next report (it goes to them, not to a new lead turn).
    waiters: int = 0
    lead: Any = None
    ctx: Any = None
    watchdog: asyncio.Task | None = None

    def total_spent(self) -> int:
        return sum(self.spent.values())

    def notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()


_LIVE: dict[str, _Live] = {}
# Strong references to background work (a bare create_task can be collected).
_BACKGROUND: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def settle() -> None:
    """Wait for background work to finish (tests)."""
    for _ in range(2000):
        pending = [t for t in _BACKGROUND if not t.done() and t is not asyncio.current_task()
                   and not getattr(t, '_swarm_watchdog', False)]
        from cognitrix.sessions import agent_chats

        if not pending and not agent_chats._TASKS:
            return
        await asyncio.sleep(0.01)
    raise AssertionError('swarm work did not settle')


def swarms_root() -> Path:
    from cognitrix.config import settings

    return (Path(settings.workdir) / 'swarms').resolve()


def running_workers(user_key: str) -> int:
    return sum(len(live.runs) + len(live.tasks) for live in _LIVE.values() if live.user_key == str(user_key))


async def workers_of(swarm_id: str) -> list[SwarmWorker]:
    rows = await SwarmWorker.find({'swarm_id': str(swarm_id)}) or []
    return sorted(rows, key=lambda w: w.position)


async def get_swarm(user_key: str, swarm_id: str) -> SwarmRun | None:
    row = await SwarmRun.get(str(swarm_id)) if swarm_id else None
    return row if row is not None and str(row.user_id) == str(user_key) else None


async def swarms_for_chat(user_key: str, session_id: str) -> list[SwarmRun]:
    rows = await SwarmRun.find({'user_id': str(user_key), 'session_id': str(session_id)}) or []
    return sorted(rows, key=lambda s: s.started_at or '', reverse=True)


async def latest_for_chat(user_key: str, session_id: str) -> SwarmRun | None:
    rows = await swarms_for_chat(user_key, session_id)
    return next((s for s in rows if s.status == 'running'), rows[0] if rows else None)


# ---------------------------------------------------------------------------
# Starting a swarm
# ---------------------------------------------------------------------------

async def start_swarm(
    user_key: str,
    lead: Any,
    specs: list[WorkerSpec],
    *,
    session_id: str = '',
    title: str = '',
    mode: str = 'fanout',
    review: bool = False,
    wake: dict[str, Any] | None = None,
    folders: list[dict[str, Any]] | None = None,
    sandbox: bool = False,
    inline: bool = False,
    ctx: Any = None,
    task_id: str = '',
    run_id: str = '',
    step_index: int = -1,
) -> SwarmRun:
    settings = await get_settings(user_key)
    limits = effective_limits(settings)
    if len(specs) > limits['max_workers']:
        raise SwarmError(
            'too_many_workers',
            f'A swarm holds at most {limits["max_workers"]} workers; you asked for {len(specs)}. '
            'Split the job into fewer, larger parts.')
    swarm = SwarmRun(
        user_id=str(user_key), session_id=str(session_id or ''),
        agent_id=str(getattr(lead, 'id', '') or ''), agent_name=str(getattr(lead, 'name', '') or 'Lead'),
        title=(title or '').strip()[:80] or 'Swarm', mode=mode if mode in ('fanout', 'best_of') else 'fanout',
        review=bool(review), wake=_clean_wake(wake), folders=list(folders or []), sandbox=bool(sandbox),
        limits={k: limits[k] for k in ('concurrency', 'per_person', 'max_workers', 'token_budget')},
        task_id=str(task_id or ''), run_id=str(run_id or ''), step_index=step_index, started_at=now_iso(),
    )
    await swarm.save()
    folder = swarms_root() / str(swarm.id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception('Could not make the swarm folder %s', folder)
    await SwarmRun.update_one({'id': swarm.id}, {'folder': str(folder)})
    swarm.folder = str(folder)
    live = _LIVE[str(swarm.id)] = _Live(swarm_id=str(swarm.id), user_key=str(user_key), inline=inline,
                                         lead=lead, ctx=ctx)
    try:
        await _add_rows(swarm, lead, specs, added_by='')
    except SwarmError:
        _LIVE.pop(str(swarm.id), None)
        await SwarmWorker.delete_many({'swarm_id': str(swarm.id)})
        await SwarmRun.delete_many({'id': swarm.id})
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if inline and run_id:
        await _reuse_finished(swarm)
    live.watchdog = _spawn(_watch(str(swarm.id)))
    live.watchdog._swarm_watchdog = True  # type: ignore[attr-defined]
    await _advance(str(swarm.id))
    return swarm


def _clean_wake(wake: dict[str, Any] | None) -> dict[str, Any]:
    wake = wake or {}
    until = str(wake.get('until') or 'all').lower()
    if until not in ('all', 'count', 'failure'):
        until = 'all'
    try:
        count = max(1, int(wake.get('count') or 1))
    except (TypeError, ValueError):
        count = 1
    return {'until': until, 'count': count}


async def _add_rows(swarm: SwarmRun, lead: Any, specs: list[WorkerSpec], *, added_by: str) -> list[SwarmWorker]:
    """Check the specs against what the swarm already has and save them."""
    from cognitrix.agents.access import find_visible_agent

    live = _LIVE.get(str(swarm.id))
    existing = await workers_of(str(swarm.id))
    limit = int((swarm.limits or {}).get('max_workers') or ceiling_workers())
    if len(existing) + len(specs) > limit:
        raise SwarmError('too_many_workers', f'This swarm is limited to {limit} workers.')
    names = {w.name for w in existing}
    order = max((w.position for w in existing), default=0)
    planned: list[tuple[WorkerSpec, str, Any, dict[str, str], str]] = []
    for spec in specs:
        base = slug(spec.name or spec.role, 'worker')
        name, n = base, 1
        while name in names:
            n += 1
            name = f'{base[:NAME_MAX - 4]}-{n}'
        names.add(name)
        agent = lead
        role = spec.role
        if role and role.lower() not in ('self', 'copy', 'me') and not (live and live.inline):
            found = (await find_visible_agent(swarm.user_id, name=role)
                     or await find_visible_agent(swarm.user_id, name=role.title()))
            if found is not None:
                agent = found
        if role.lower() in ('self', 'copy', 'me'):
            role = ''
        try:
            model = parse_model(spec.model, getattr(agent, 'llm', None))
            if model and getattr(agent, 'llm', None) is not None:
                from cognitrix.providers.base import build_turn_llm

                build_turn_llm(agent.llm, model['provider'], model['model'])
        except ValueError as exc:
            raise SwarmError('bad_model', f'Worker "{name}": {exc}') from None
        planned.append((spec, name, agent, model, role))
    for spec, name, _agent, _model, _role in planned:
        unknown = [d for d in spec.depends_on if d not in names or d == name]
        if unknown:
            raise SwarmError('bad_dependency',
                             f'Worker "{name}" depends on {", ".join(unknown)}, which is not a worker of this swarm')
    # Workers that wait for each other in a circle would never start.
    graph = {w.name: list(w.depends_on or []) for w in existing}
    graph.update({name: list(spec.depends_on) for spec, name, _a, _m, _r in planned})
    state: dict[str, int] = {}

    def circular(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        found = any(circular(dep) for dep in graph.get(node, ()))
        state[node] = 2
        return found

    for _spec, name, _a, _m, _r in planned:
        if circular(name):
            raise SwarmError('bad_dependency',
                             f'Worker "{name}" is in a circle of workers that wait for each other. '
                             'Order them so that one can start first.')
    rows: list[SwarmWorker] = []
    for spec, name, agent, model, role in planned:
        order += 1
        row = SwarmWorker(
            swarm_id=str(swarm.id), user_id=swarm.user_id, name=name,
            role=role, persona=spec.persona,
            agent_id=str(getattr(agent, 'id', '') or ''), agent_name=str(getattr(agent, 'name', '') or ''),
            model=model, brief=spec.brief, depends_on=list(dict.fromkeys(spec.depends_on)),
            may_edit=spec.may_edit, position=order, added_by=added_by,
        )
        await row.save()
        rows.append(row)
    return rows


async def add_workers(user_key: str, swarm_id: str, specs: list[WorkerSpec], *, added_by: str = '') -> list[SwarmWorker]:
    """More jobs for a running swarm (the lead, or a worker through Add Work)."""
    swarm = await get_swarm(user_key, swarm_id)
    live = _LIVE.get(str(swarm_id))
    if swarm is None or live is None or swarm.status != 'running':
        raise SwarmError('not_running', 'That swarm is not running any more.')
    if added_by:
        used = live.added.get(added_by, 0)
        if used + len(specs) > MAX_ADDED_PER_WORKER:
            raise SwarmError('too_many_added',
                             f'A worker may add at most {MAX_ADDED_PER_WORKER} jobs. Tell the lead what is left instead.')
        live.added[added_by] = used + len(specs)
    async with live.lock:
        rows = await _add_rows(swarm, live.lead or await _lead_agent(swarm), specs, added_by=added_by)
    await _advance(str(swarm_id))
    return rows


async def _lead_agent(swarm: SwarmRun) -> Any:
    from cognitrix.agents.access import find_visible_agent

    return await find_visible_agent(swarm.user_id, agent_id=swarm.agent_id)


def _brief_key(worker: SwarmWorker) -> str:
    return hashlib.sha256(f'{worker.role}\n{worker.persona}\n{worker.brief}'.encode()).hexdigest()


async def _reuse_finished(swarm: SwarmRun) -> None:
    """A task step that is run again (after a restart or a retry) starts its
    swarm again: workers whose job already finished in the earlier attempt
    keep that result instead of doing it twice."""
    earlier = [s for s in await SwarmRun.find({'run_id': swarm.run_id}) or []
               if str(s.id) != str(swarm.id) and s.step_index == swarm.step_index]
    done: dict[str, SwarmWorker] = {}
    for old in earlier:
        for worker in await workers_of(str(old.id)):
            if worker.status == 'done' and worker.result_path and Path(worker.result_path).is_file():
                done.setdefault(_brief_key(worker), worker)
    if not done:
        return
    for worker in await workers_of(str(swarm.id)):
        old = done.get(_brief_key(worker))
        if old is None:
            continue
        await SwarmWorker.update_one({'id': worker.id}, {
            'status': 'done', 'summary': old.summary, 'result_path': old.result_path,
            'tokens': 0, 'started_at': old.started_at, 'finished_at': old.finished_at,
        })


# ---------------------------------------------------------------------------
# Moving the swarm forward
# ---------------------------------------------------------------------------

async def _advance(swarm_id: str) -> None:
    """Start whatever may start now, then tell the lead if it is time to."""
    live = _LIVE.get(swarm_id)
    if live is None:
        return
    async with live.lock:
        swarm = await SwarmRun.get(swarm_id)
        if swarm is None:
            return
        workers = await workers_of(swarm_id)
        if swarm.status == 'running':
            await _start_ready(live, swarm, workers)
            workers = await workers_of(swarm_id)
        all_ended = all(w.status in ENDED for w in workers)
        if all_ended and swarm.status == 'running':
            if swarm.mode == 'best_of' and not swarm.judge:
                await _judge(swarm, workers)
            await SwarmRun.update_one({'id': swarm.id}, {
                'status': 'done', 'finished_at': now_iso(),
                'tokens': sum(w.tokens for w in workers),
            })
            swarm.status = 'done'
        report = None
        if (not live.inline and not live.waiters and swarm.session_id and swarm.status != 'stopped'
                and _due(swarm, workers)):
            report = await _take_report(swarm, workers)
    live.notify()
    if report:
        _spawn(_deliver(swarm, report))
    if swarm.status != 'running' and not live.runs and not live.tasks:
        _retire(swarm_id)


def _retire(swarm_id: str) -> None:
    live = _LIVE.pop(swarm_id, None)
    if live is not None and live.watchdog is not None:
        live.watchdog.cancel()


def _blocked_by(worker: SwarmWorker, by_name: dict[str, SwarmWorker]) -> tuple[bool, str]:
    """(waiting, failed dependency name)."""
    waiting = False
    for dep in worker.depends_on:
        other = by_name.get(dep)
        if other is None:
            continue
        if other.status in ('failed', 'stopped'):
            return False, dep
        if other.status != 'done':
            waiting = True
    return waiting, ''


async def _start_ready(live: _Live, swarm: SwarmRun, workers: list[SwarmWorker]) -> None:
    limits = swarm.limits or {}
    per_swarm = int(limits.get('concurrency') or 4)
    per_person = int(limits.get('per_person') or ceiling_concurrency())
    budget = int(limits.get('token_budget') or 0)
    by_name = {w.name: w for w in workers}
    spent = sum(w.tokens for w in workers if w.name not in live.spent) + live.total_spent()
    if budget and spent >= budget and not live.over_budget:
        live.over_budget = True
        await _note(swarm, f'The swarm reached its token budget ({budget:,}). No more workers were started.')
    editors = [w for w in workers if w.may_edit]
    for worker in workers:
        if worker.status != 'queued':
            continue
        waiting, failed = _blocked_by(worker, by_name)
        if failed:
            await _end_worker(worker, 'failed', error=f'It needed "{failed}", which did not finish.')
            continue
        if live.over_budget:
            await _end_worker(worker, 'stopped', error='Not started: the swarm reached its token budget.')
            continue
        if waiting:
            continue
        active = len(live.runs) + len(live.tasks)
        if active >= per_swarm or running_workers(swarm.user_id) >= per_person:
            break
        if worker.may_edit and not worker.worktree:
            # Editors without a copy of their own take turns.
            busy = any(e.name in live.runs or e.name in live.tasks for e in editors
                       if e.name != worker.name and not e.worktree)
            if busy:
                continue
        try:
            await _start_worker(live, swarm, worker, by_name, shares_folder=len(editors) > 1)
        except Exception as exc:
            logger.exception('Could not start swarm worker %s', worker.name)
            await _end_worker(worker, 'failed', error=f'Could not start: {exc}')


async def _end_worker(worker: SwarmWorker, status: str, *, error: str = '', **more: Any) -> None:
    changes = {'status': status, 'error': error[:1000], 'finished_at': now_iso(), **more}
    await SwarmWorker.update_one({'id': worker.id}, changes)
    for key, value in changes.items():
        setattr(worker, key, value)


async def _note(swarm: SwarmRun, text: str) -> None:
    notes = [*(swarm.notes or []), text][-10:]
    swarm.notes = notes
    await SwarmRun.update_one({'id': swarm.id}, {'notes': notes})


def worker_brief(swarm: SwarmRun, worker: SwarmWorker, by_name: dict[str, SwarmWorker], *,
                 edit_note: str = '') -> str:
    """Everything a worker knows: it sees only this message."""
    who = f' Your role: {worker.role}.' if worker.role else ''
    lines = [
        f'{swarm.agent_name} (agent) started this chat. You are "{worker.name}", one of several workers '
        f'on a bigger job ("{swarm.title}").{who}',
    ]
    if worker.persona:
        lines.append(worker.persona)
    lines += ['', '## Your job', worker.brief]
    deps = [by_name[d] for d in worker.depends_on if d in by_name and by_name[d].status == 'done']
    if deps:
        lines += ['', '## What teammates already found']
        for dep in deps:
            lines.append(f'### {dep.name}\n{dep.summary or "(no summary)"}')
        lines.append('Use Swarm Team (action "result", worker name) to read a teammate\'s full result.')
    lines += [
        '', '## How to work',
        '- This message is all you know about the job. Make sensible assumptions and list them; '
        'do not ask the person questions unless you cannot go on without an answer.',
        '- Stay inside your job. Other workers are doing the other parts.',
        '- Do what the job needs and no more: stop looking things up once you can answer it well. '
        'Every extra step costs tokens.',
        edit_note or '- You may read the working folders but not change files or run commands.',
        '- Finish with your complete result as your reply. Begin it with "Summary:" and at most five lines '
        'that say what you found or did, then the details. The reply is saved for the lead as a file, '
        'so put everything worth keeping in it. Files you save in your scratch folder are kept with it.',
        '- You cannot start workers yourself. If you find a separate job that is needed, add it with '
        'Swarm Team (action "add_work"), or say so in your result.',
    ]
    return '\n'.join(lines)


async def _start_worker(live: _Live, swarm: SwarmRun, worker: SwarmWorker,
                        by_name: dict[str, SwarmWorker], *, shares_folder: bool) -> None:
    await SwarmWorker.update_one({'id': worker.id}, {
        'status': 'running', 'started_at': worker.started_at or now_iso(), 'attempts': worker.attempts + 1,
    })
    worker.status = 'running'
    worker.attempts += 1
    if live.inline:
        text = worker_brief(swarm, worker, by_name, edit_note=(
            '- You may change files in the working folders for this job.' if worker.may_edit else ''))
        task = asyncio.get_running_loop().create_task(_run_inline(live, swarm, worker, text))
        live.tasks[worker.name] = task
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        return

    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    agent = await find_visible_agent(swarm.user_id, agent_id=worker.agent_id)
    if agent is None:
        raise SwarmError('agent_not_found', 'The agent for this worker no longer exists')
    lead_session = await Session.get(swarm.session_id) if swarm.session_id else None
    folders, tree, edit_note = await _worker_folders(swarm, worker, shares_folder=shares_folder)
    started_by = {
        'agent_id': swarm.agent_id, 'agent_name': swarm.agent_name,
        'session_id': swarm.session_id or None,
        'depth': agent_chats.depth_of(lead_session) + 1 if lead_session is not None else 1,
        'report_back': False, 'exchanges': 1,
        'via': 'swarm', 'swarm_id': str(swarm.id), 'worker': worker.name, 'role': worker.role,
    }
    session = await agent_chats.create_chat(swarm.user_id, agent, started_by, folders=folders)
    changes: dict[str, Any] = {'title': f'{swarm.title}: {worker.name}'[:120]}
    if swarm.sandbox:
        changes['run_in_sandbox'] = True
    try:
        from cognitrix.common.folders import scratch_folder

        scratch = scratch_folder(session.scratch_key()).to_dict()
        scratch['primary'] = not folders
        changes['folders'] = [*(folders or []), scratch]
    except Exception:
        logger.debug('No scratch folder for worker %s', worker.name, exc_info=True)
    await Session.update_one({'id': str(session.id)}, changes)
    for key, value in changes.items():
        setattr(session, key, value)
    row_changes: dict[str, Any] = {'chat_id': str(session.id)}
    if tree:
        row_changes['worktree'] = tree
    await SwarmWorker.update_one({'id': worker.id}, row_changes)
    worker.chat_id = str(session.id)
    worker.worktree = tree or worker.worktree
    text = worker_brief(swarm, worker, by_name, edit_note=edit_note)
    _run_chat_turn(live, swarm, worker, agent, session, text)


def _run_chat_turn(live: _Live, swarm: SwarmRun, worker: SwarmWorker, agent: Any, session: Any, text: str) -> None:
    from cognitrix.sessions import agent_chats
    from cognitrix.tools.utils import HostProcessMode, ToolExecutionContext, signed_in_host_process_mode

    swarm_id, name = str(swarm.id), worker.name
    model = dict(worker.model or {})

    def count(usage: dict) -> None:
        try:
            used = int(usage.get('prompt_tokens') or 0) + int(usage.get('completion_tokens') or 0)
        except (TypeError, ValueError):
            used = 0
        live.spent[name] = live.spent.get(name, 0) + max(0, used)

    def setup() -> float:
        from cognitrix import usage

        from cognitrix.providers.base import build_turn_llm, turn_llm_override

        usage.set_sink(count)
        # Set either way: this turn may have been started from another worker's context.
        turn_llm_override.set(
            (agent.llm.id, build_turn_llm(agent.llm, model['provider'], model['model']))
            if model and getattr(agent, 'llm', None) is not None else None)
        return worker_timeout()

    async def done(status: str, reply: str) -> None:
        await _worker_turn_ended(swarm_id, name, status, reply)

    ctx = ToolExecutionContext(
        session_id=str(session.id), user_id=swarm.user_id, agent_id=str(agent.id),
        host_process_mode=signed_in_host_process_mode() if worker.may_edit else HostProcessMode.DENY,
    )
    live.spent.setdefault(name, worker.tokens)
    run = agent_chats.run_in_background(
        swarm.user_id, agent, session, text, on_done=done, setup=setup, tool_context=ctx, swarm_id=swarm_id)
    live.runs[name] = run
    live.activity[name] = (0, time.monotonic())


async def _worker_folders(swarm: SwarmRun, worker: SwarmWorker, *, shares_folder: bool
                          ) -> tuple[list[dict[str, Any]] | None, dict[str, Any], str]:
    """The worker chat's folders, a worktree when it gets one, and the line
    of its brief that says what it may change."""
    from cognitrix.common.folders import READ, WRITE

    folders = [dict(f) for f in (swarm.folders or []) if isinstance(f, dict)]
    if not worker.may_edit:
        return [{**f, 'access': READ} for f in folders] or None, {}, ''
    writable = [f for f in folders if str(f.get('access')) == WRITE]
    if not writable:
        return folders or None, {}, ('- You may change files, but this chat has no folder it can write to '
                                     'apart from your scratch folder.')
    tree: dict[str, Any] = dict(worker.worktree or {})
    if shares_folder and not swarm.sandbox and not tree:
        target = next((f for f in writable if f.get('primary')), writable[0])
        repo = await asyncio.to_thread(worktrees.repo_root, str(target.get('path') or ''))
        if repo:
            from cognitrix.common.folders import FolderError, validate_folder

            # Next to the project first; inside it (ignored by git) when this
            # server only allows folders under the project.
            for inside in (False, True):
                made: dict[str, Any] = {}
                try:
                    made = await asyncio.to_thread(worktrees.create, repo, str(swarm.id), worker.name, inside=inside)
                    validate_folder(made['path'], WRITE)
                    tree = {**made, 'folder': str(target.get('path') or '')}
                    break
                except Exception as exc:
                    if made:
                        await asyncio.to_thread(worktrees.remove, made)
                    if not isinstance(exc, FolderError):
                        logger.info('No separate copy for worker %s: %s', worker.name, exc)
                        break
    if tree:
        out = []
        for f in folders:
            if str(f.get('path')) == tree.get('folder'):
                out.append({**f, 'path': tree['path'], 'access': WRITE})
            else:
                out.append({**f, 'access': READ})
        note = (f'- You may change files. You work in your own copy of the project at {tree["path"]} '
                f'(git branch {tree["branch"]}), so other workers cannot clash with you. It starts from the '
                'last commit: changes that were not committed are not in it. Your changes are committed '
                'for you when you finish, and the lead merges them.')
        return out, tree, note
    return folders, {}, '- You may change files in the working folders for this job. Change only what the job needs.'


# ---------------------------------------------------------------------------
# A worker's turn ended
# ---------------------------------------------------------------------------

def split_summary(reply: str) -> str:
    """The "Summary:" lines a worker's reply starts with, else its opening."""
    text = reply.strip()
    match = re.search(r'(?im)^\s*(?:#+\s*)?\**summary\**\s*:?\**\s*', text)
    if match and match.start() < 400:
        rest = text[match.end():]
        block = re.split(r'\n\s*\n', rest.strip(), maxsplit=1)[0]
        heading_alone = '\n' in match.group(0).lstrip()
        if heading_alone and len(block) < 40 and len(rest.strip()) > len(block):
            block = rest.strip()
        text = block
    return text[:SUMMARY_MAX_CHARS].strip()


def _strip_think(text: str) -> str:
    from cognitrix.teams.group_chat import _strip_think as strip

    return strip(text or '').strip()


async def _worker_turn_ended(swarm_id: str, name: str, status: str, reply: str) -> None:
    live = _LIVE.get(swarm_id)
    if live is None:
        return
    run = live.runs.pop(name, None)
    live.tasks.pop(name, None)
    live.activity.pop(name, None)
    swarm = await SwarmRun.get(swarm_id)
    worker = await SwarmWorker.find_one({'swarm_id': swarm_id, 'name': name})
    if swarm is None or worker is None:
        return
    tokens = live.spent.get(name, worker.tokens)
    reply = _strip_think(reply)
    error = ''
    if status == 'error':
        error = _run_error(run) or 'The worker failed before it replied.'
    elif status == 'complete' and not reply:
        status, error = 'error', 'The worker ended without a reply.'

    if status == 'error' and _RATE_LIMIT.search(error) and worker.attempts <= RATE_LIMIT_RETRIES \
            and swarm.status == 'running' and not live.inline:
        delay = RATE_LIMIT_DELAYS[min(worker.attempts - 1, len(RATE_LIMIT_DELAYS) - 1)]
        await SwarmWorker.update_one({'id': worker.id}, {'tokens': tokens, 'error': error[:1000]})
        _spawn(_retry_later(swarm_id, name, delay))
        return

    if status == 'complete' and swarm.review and swarm.status == 'running':
        rounds = int((worker.review or {}).get('rounds') or 0)
        await SwarmWorker.update_one({'id': worker.id}, {'status': 'reviewing', 'tokens': tokens})
        verdict, notes = await _review(swarm, worker, reply)
        review = {'verdict': verdict, 'notes': notes[:2000], 'rounds': rounds + 1}
        await SwarmWorker.update_one({'id': worker.id}, {'review': review})
        worker.review = review
        if verdict == 'revise' and rounds == 0 and not live.inline and await _send_back(live, swarm, worker, notes):
            return

    if status == 'complete':
        path = await asyncio.to_thread(_save_result, swarm, worker, reply)
        changes: dict[str, Any] = {
            'status': 'done', 'summary': split_summary(reply), 'result_path': path, 'error': '',
            'tokens': tokens, 'finished_at': now_iso(), 'stalled': False,
        }
        if worker.worktree and not worker.worktree.get('merged'):
            try:
                changes['worktree'] = await asyncio.to_thread(
                    worktrees.commit, worker.worktree, f'Swarm worker {worker.name}: {swarm.title}')
            except Exception as exc:
                logger.info('Could not commit worker %s changes: %s', worker.name, exc)
        await SwarmWorker.update_one({'id': worker.id}, changes)
    else:
        await SwarmWorker.update_one({'id': worker.id}, {
            'status': 'stopped' if status == 'stopped' else 'failed',
            'error': '' if status == 'stopped' else error[:1000],
            'tokens': tokens, 'finished_at': now_iso(), 'stalled': False,
        })
    changes = {'tokens': sum(w.tokens for w in await workers_of(swarm_id))}
    if swarm.status == 'running':
        changes['reported'] = [n for n in (swarm.reported or []) if n != name]
    await SwarmRun.update_one({'id': swarm.id}, changes)
    await _advance(swarm_id)
    # The slot this worker held may be what the person's other swarms wait for.
    for other_id, other in list(_LIVE.items()):
        if other_id != swarm_id and other.user_key == live.user_key:
            _spawn(_advance(other_id))


def _run_error(run: Any) -> str:
    events = getattr(run, 'events', None) or []
    for event in reversed(events):
        if event.get('type') == 'error' and event.get('content'):
            return str(event['content'])[:1000]
    return ''


async def _retry_later(swarm_id: str, name: str, delay: float) -> None:
    await asyncio.sleep(delay)
    live = _LIVE.get(swarm_id)
    swarm = await SwarmRun.get(swarm_id)
    worker = await SwarmWorker.find_one({'swarm_id': swarm_id, 'name': name})
    if live is None or swarm is None or worker is None or swarm.status != 'running' or worker.status != 'running':
        if worker is not None and worker.status == 'running':
            await _end_worker(worker, 'failed', error=worker.error or 'The swarm ended before it could retry.')
            await _advance(swarm_id)
        return
    await SwarmWorker.update_one({'id': worker.id}, {'attempts': worker.attempts + 1})
    worker.attempts += 1
    if not await _continue_chat(live, swarm, worker,
                                'The model was busy and your last attempt was cut off. Carry on with the job above '
                                'and finish with your complete result.'):
        await _end_worker(worker, 'failed', error=worker.error or 'Could not retry.')
        await _advance(swarm_id)


async def _continue_chat(live: _Live, swarm: SwarmRun, worker: SwarmWorker, text: str) -> bool:
    """Another turn in the worker's own chat."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions.base import Session

    agent = await find_visible_agent(swarm.user_id, agent_id=worker.agent_id)
    session = await Session.get(worker.chat_id) if worker.chat_id else None
    if agent is None or session is None:
        return False
    _run_chat_turn(live, swarm, worker, agent, session, text)
    return True


async def _send_back(live: _Live, swarm: SwarmRun, worker: SwarmWorker, notes: str) -> bool:
    await SwarmWorker.update_one({'id': worker.id}, {'status': 'running'})
    worker.status = 'running'
    return await _continue_chat(
        live, swarm, worker,
        'A reviewer checked your result and asks for changes before it goes to the lead:\n\n'
        f'{notes}\n\nFix these and reply with the complete corrected result (begin with "Summary:").')


def _safe_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)[:60] or 'worker'


def worker_folder(swarm: SwarmRun, worker: SwarmWorker) -> Path:
    return Path(swarm.folder or (swarms_root() / str(swarm.id))) / _safe_name(worker.name)


def _save_result(swarm: SwarmRun, worker: SwarmWorker, reply: str) -> str:
    """Write the worker's reply to its folder and keep its scratch files with it."""
    folder = worker_folder(swarm, worker)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'result.md'
        path.write_text(reply, encoding='utf-8')
    except OSError:
        logger.exception('Could not save the result of worker %s', worker.name)
        return ''
    if worker.chat_id and not swarm.sandbox:
        try:
            from cognitrix.common.folders import scratch_path

            scratch = scratch_path(f'chat-{worker.chat_id}')
            if scratch.is_dir():
                total = 0
                files = sorted(p for p in scratch.rglob('*') if p.is_file())[:MAX_COPIED_FILES]
                for item in files:
                    size = item.stat().st_size
                    if total + size > MAX_COPIED_BYTES:
                        break
                    total += size
                    target = folder / 'files' / item.relative_to(scratch)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
        except OSError:
            logger.debug('Could not copy scratch files for worker %s', worker.name, exc_info=True)
    return str(path)


# ---------------------------------------------------------------------------
# Inline workers (inside a task run)
# ---------------------------------------------------------------------------

async def _run_inline(live: _Live, swarm: SwarmRun, worker: SwarmWorker, text: str) -> None:
    from cognitrix import usage
    from cognitrix.sessions.base import Session
    from cognitrix.tools.swarm import SWARM_TOOL_CATEGORIES
    from cognitrix.tools.utils import HostProcessMode, current_execution_context

    name = worker.name
    status, chunks = 'complete', []

    def count(used: dict) -> None:
        try:
            total = int(used.get('prompt_tokens') or 0) + int(used.get('completion_tokens') or 0)
        except (TypeError, ValueError):
            total = 0
        live.spent[name] = live.spent.get(name, 0) + max(0, total)

    async def capture(payload=None, *args, **kwargs) -> None:
        if isinstance(payload, dict) and payload.get('type') != 'tool' and payload.get('content'):
            chunks.append(str(payload['content']))

    try:
        lead = live.lead
        tools = [t for t in (getattr(lead, 'tools', None) or [])
                 if str(getattr(t, 'category', '')).lower() not in SWARM_TOOL_CATEGORIES]
        update: dict[str, Any] = {'tools': tools}
        if worker.role or worker.persona:
            extra = '\n\n## Your role in this job\n' + ' '.join(x for x in (worker.role, worker.persona) if x)
            update['system_prompt'] = (getattr(lead, 'system_prompt', '') or '') + extra
        agent = lead.model_copy(update=update)
        ctx = live.ctx or current_execution_context()
        if not worker.may_edit:
            from cognitrix.common.folders import read_only

            ctx = dataclasses.replace(ctx, host_process_mode=HostProcessMode.DENY,
                                      folders=read_only(ctx.folders) if ctx.folders else ())
        usage.set_sink(count)  # this task has its own copy of the context
        from cognitrix.providers.base import build_turn_llm, turn_llm_override

        model = dict(worker.model or {})
        # Set either way: this task may have been started from another worker's context.
        turn_llm_override.set(
            (lead.llm.id, build_turn_llm(lead.llm, model['provider'], model['model']))
            if model and getattr(lead, 'llm', None) is not None else None)
        session = Session(agent_id=str(getattr(lead, 'id', '') or ''), user_id=swarm.user_id or None)
        await asyncio.wait_for(
            session(text, agent, 'task', True, capture, {}, save_history=False, tool_context=ctx,
                    record_history=True, persist_history=False, compact_history=False),
            timeout=worker_timeout(),
        )
    except asyncio.CancelledError:
        status = 'stopped'
    except TimeoutError:
        status, chunks = 'error', []
        live.runs[name] = _Failure('timed out')
    except Exception as exc:
        logger.exception('Inline swarm worker %s failed', name)
        status, chunks = 'error', []
        live.runs[name] = _Failure(str(exc))
    await asyncio.shield(_worker_turn_ended(str(swarm.id), name, status, ''.join(chunks)))


@dataclass
class _Failure:
    """Stands in for a chat run so an inline failure keeps its message."""
    message: str

    @property
    def events(self) -> list[dict[str, Any]]:
        return [{'type': 'error', 'content': self.message}]


# ---------------------------------------------------------------------------
# Quality gate and best-of-N
# ---------------------------------------------------------------------------

async def _ask(llm: Any, system: str, prompt: str) -> str:
    """One plain model call, no tools, no chat."""
    messages = [
        {'role': 'system', 'type': 'text', 'content': system},
        {'role': 'User', 'type': 'text', 'content': prompt},
    ]
    try:
        response = await asyncio.wait_for(llm(messages, stream=False), timeout=180)
    except Exception:
        logger.exception('Swarm helper model call failed')
        return ''
    if response is None or getattr(response, 'error', None):
        return ''
    return _strip_think(getattr(response, 'llm_response', '') or '')


async def _judge_llm(swarm: SwarmRun) -> Any:
    """The model that reviews and judges: the person's Reviewer agent when
    they have one, else the lead's own."""
    live = _LIVE.get(str(swarm.id))
    if live is not None and live.inline:
        return getattr(live.lead, 'llm', None)
    from cognitrix.agents.access import find_visible_agent

    reviewer = await find_visible_agent(swarm.user_id, name='Reviewer')
    if reviewer is not None and getattr(reviewer, 'llm', None) is not None:
        return reviewer.llm
    lead = (live.lead if live is not None else None) or await _lead_agent(swarm)
    return getattr(lead, 'llm', None)


async def _with_owner(swarm: SwarmRun, coro_fn):
    """Run a helper model call so its tokens count for the person."""
    from cognitrix import usage

    token = usage.set_owner(swarm.user_id, swarm.agent_id)
    try:
        return await coro_fn()
    finally:
        usage.reset_owner(token)


async def _review(swarm: SwarmRun, worker: SwarmWorker, reply: str) -> tuple[str, str]:
    """('pass' | 'revise', notes). A reviewer that cannot be reached passes."""
    llm = await _judge_llm(swarm)
    if llm is None:
        return 'pass', ''
    prompt = (
        f'A worker was given this job:\n\n{worker.brief[:4000]}\n\n'
        f'This is its result:\n\n{reply[:16000]}\n\n'
        'Check it against the job: is anything asked for missing, clearly wrong, unsupported or off the brief? '
        'Reply with the single word PASS on the first line if it can go to the lead as it is. Otherwise reply '
        'REVISE on the first line, then a short list of exactly what must be fixed. Do not ask for polish.'
    )
    text = await _with_owner(swarm, lambda: _ask(
        llm, 'You are a strict but fair reviewer of work done by another agent. Reply exactly as instructed.', prompt))
    first = text.strip().splitlines()[0].strip().upper() if text.strip() else 'PASS'
    if first.startswith('REVISE'):
        return 'revise', text.strip().split('\n', 1)[1].strip() if '\n' in text.strip() else 'Check the result against the brief.'
    return 'pass', ''


async def _judge(swarm: SwarmRun, workers: list[SwarmWorker]) -> None:
    """best_of: pick the best of the finished attempts."""
    done = [w for w in workers if w.status == 'done' and w.result_path]
    judge: dict[str, Any] = {'winner': '', 'verdict': ''}
    if len(done) == 1:
        judge = {'winner': done[0].name, 'verdict': 'Only one attempt finished.'}
    elif len(done) > 1:
        llm = await _judge_llm(swarm)
        parts = []
        for w in done:
            body = await asyncio.to_thread(_read_text, w.result_path, 8000)
            parts.append(f'## Attempt "{w.name}"\n{body}')
        prompt = (
            f'Several workers were given the same job:\n\n{done[0].brief[:4000]}\n\n'
            + '\n\n'.join(parts)
            + '\n\nPick the attempt that does the job best. Reply with "WINNER: <attempt name>" on the first line, '
              'then two to five lines on why, and anything the other attempts got right that the winner missed.'
        )
        text = await _with_owner(swarm, lambda: _ask(
            llm, 'You judge competing attempts at one job. Reply exactly as instructed.', prompt)) if llm else ''
        match = re.search(r'WINNER:\s*"?([A-Za-z0-9_.-]+)', text or '')
        names = {w.name for w in done}
        winner = match.group(1) if match and match.group(1) in names else done[0].name
        judge = {'winner': winner, 'verdict': (text or 'The judge could not be reached; the first attempt was taken.')[:3000]}
    await SwarmRun.update_one({'id': swarm.id}, {'judge': judge})
    swarm.judge = judge


def _read_text(path: str, limit: int, offset: int = 0) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            if offset:
                handle.seek(0)
                text = handle.read()
                return text[offset:offset + limit]
            return handle.read(limit)
    except OSError:
        return ''


# ---------------------------------------------------------------------------
# Telling the lead
# ---------------------------------------------------------------------------

def _due(swarm: SwarmRun, workers: list[SwarmWorker]) -> bool:
    unreported = [w for w in workers if w.status in ENDED and w.name not in (swarm.reported or [])]
    if not unreported and not swarm.notes:
        return False
    if all(w.status in ENDED for w in workers):
        return bool(unreported)
    if swarm.notes:
        return True
    wake = swarm.wake or {}
    if wake.get('until') == 'count':
        return len(unreported) >= int(wake.get('count') or 1)
    if wake.get('until') == 'failure':
        return any(w.status == 'failed' for w in unreported)
    return False


def ledger(swarm: SwarmRun, workers: list[SwarmWorker]) -> dict[str, Any]:
    """Where the swarm stands, in numbers."""
    counts = {status: 0 for status in ('queued', 'running', 'reviewing', 'done', 'failed', 'stopped')}
    for w in workers:
        counts[w.status] = counts.get(w.status, 0) + 1
    live = _LIVE.get(str(swarm.id))
    tokens = sum((live.spent.get(w.name, w.tokens) if live else w.tokens) for w in workers)
    return {
        'total': len(workers), **counts,
        'stalled': [w.name for w in workers if w.stalled and w.status == 'running'],
        'tokens': tokens, 'token_budget': int((swarm.limits or {}).get('token_budget') or 0),
        'finished': all(w.status in ENDED for w in workers),
    }


def _ledger_line(state: dict[str, Any]) -> str:
    parts = [f'{state["done"]} of {state["total"]} done']
    for key, label in (('failed', 'failed'), ('stopped', 'stopped'), ('running', 'still working'),
                       ('reviewing', 'being reviewed'), ('queued', 'waiting to start')):
        if state.get(key):
            parts.append(f'{state[key]} {label}')
    if state['stalled']:
        parts.append(f'stalled: {", ".join(state["stalled"])}')
    parts.append(f'{state["tokens"]:,} tokens used')
    return ', '.join(parts)


def report_text(swarm: SwarmRun, workers: list[SwarmWorker], fresh: list[SwarmWorker], *, prefix: str = '') -> str:
    state = ledger(swarm, workers)
    lines = [f'{prefix}Swarm "{swarm.title}" (swarm id {swarm.id}) report: {_ledger_line(state)}.']
    for note in swarm.notes or []:
        lines.append(f'Note: {note}')
    for w in fresh:
        role = f' ({w.role})' if w.role else ''
        if w.status == 'done':
            head = f'### {w.name}{role}: done'
            extra = []
            if (w.review or {}).get('verdict') == 'revise':
                extra.append('the reviewer still had doubts: ' + str(w.review.get('notes') or '')[:300])
            tree = w.worktree or {}
            if tree.get('changed') and not tree.get('merged'):
                extra.append(f'changed {len(tree["changed"])} file(s) in its own copy (branch {tree["branch"]}); '
                             f'merge with Swarm (action "merge", worker "{w.name}")')
            lines.append(head)
            lines.append(w.summary or '(no summary)')
            lines += [f'({e})' for e in extra]
        elif w.status == 'failed':
            lines.append(f'### {w.name}{role}: failed\n{w.error or "No reason given."}')
        else:
            lines.append(f'### {w.name}{role}: stopped\n{w.error or "Stopped before it finished."}')
    if swarm.mode == 'best_of' and swarm.judge:
        lines.append(f'### Judge\nBest attempt: {swarm.judge.get("winner") or "(none)"}\n'
                     f'{swarm.judge.get("verdict") or ""}')
    if state['finished']:
        lines.append(
            'Every worker has ended. Each summary is short on purpose: read a full result with Swarm '
            '(action "result", worker name) where it matters, check the claims you will rely on, send weak '
            'parts back with Swarm (action "message"), then give the person one combined answer. '
            'Do not reply only to acknowledge this report.')
    else:
        lines.append(
            'The rest are still working; you will get another report. Decide now whether the plan still '
            'holds: stop or redirect workers that are stalled or no longer needed (Swarm, action "stop" or '
            '"message"), and do not repeat work that is under way.')
    return '\n\n'.join(lines)


async def _take_report(swarm: SwarmRun, workers: list[SwarmWorker], *, prefix: str | None = None) -> str | None:
    """Build the report for workers the lead has not heard about, and mark them told."""
    from cognitrix.sessions.agent_chats import REPORT_PREFIX

    fresh = [w for w in workers if w.status in ENDED and w.name not in (swarm.reported or [])]
    if not fresh and not swarm.notes:
        return None
    text = report_text(swarm, workers, fresh, prefix=REPORT_PREFIX if prefix is None else prefix)
    reported = [*(swarm.reported or []), *(w.name for w in fresh)]
    await SwarmRun.update_one({'id': swarm.id}, {'reported': reported, 'notes': []})
    swarm.reported, swarm.notes = reported, []
    return text


async def _deliver(swarm: SwarmRun, text: str) -> None:
    from cognitrix.sessions import agent_chats

    try:
        outcome = await agent_chats.deliver_report(
            swarm.user_id, swarm.agent_id, swarm.session_id, text, sender=f'The swarm "{swarm.title}"')
    except Exception:
        logger.exception('Could not deliver the report of swarm %s', swarm.id)
        outcome = 'dropped'
    if outcome == 'dropped':
        try:
            await agent_chats._alert_once(
                swarm.user_id, swarm.session_id, f'The swarm "{swarm.title}" has results',
                'Its lead chat was busy. Open the chat and ask for the swarm results.')
        except Exception:
            logger.debug('Could not alert about swarm %s', swarm.id, exc_info=True)


async def wait_for_report(user_key: str, swarm_id: str, timeout: float) -> str | None:
    """Wait up to ``timeout`` seconds for news the lead has not had, and
    return it as a report (marked told). None when nothing came in time."""
    deadline = time.monotonic() + max(0.0, timeout)
    held = _LIVE.get(str(swarm_id))
    if held is not None:
        held.waiters += 1
    try:
        while True:
            swarm = await get_swarm(user_key, swarm_id)
            if swarm is None:
                return None
            live = _LIVE.get(str(swarm_id))
            workers = await workers_of(str(swarm_id))
            if _due(swarm, workers):
                lock = (live or held).lock if (live or held) is not None else asyncio.Lock()
                async with lock:
                    swarm = await SwarmRun.get(str(swarm_id))
                    text = await _take_report(swarm, await workers_of(str(swarm_id)), prefix='')
                if text:
                    return text
            remaining = deadline - time.monotonic()
            if live is None or remaining <= 0:
                return None
            changed = live.changed
            try:
                await asyncio.wait_for(changed.wait(), timeout=min(remaining, 5.0))
            except TimeoutError:
                pass
    finally:
        if held is not None:
            held.waiters -= 1
            if str(swarm_id) in _LIVE:
                # A report that came due as the wait gave up still reaches the lead.
                _spawn(_advance(str(swarm_id)))


async def set_wake(user_key: str, swarm_id: str, wake: dict[str, Any]) -> None:
    swarm = await get_swarm(user_key, swarm_id)
    if swarm is None:
        raise SwarmError('swarm_not_found', 'No such swarm.')
    await SwarmRun.update_one({'id': swarm.id}, {'wake': _clean_wake(wake)})
    await _advance(str(swarm_id))


# ---------------------------------------------------------------------------
# Watching for stalls
# ---------------------------------------------------------------------------

async def _watch(swarm_id: str) -> None:
    try:
        while swarm_id in _LIVE:
            await asyncio.sleep(WATCH_SECONDS)
            await check_stalls(swarm_id)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception('Swarm watchdog failed')


async def check_stalls(swarm_id: str) -> list[str]:
    """Flag running workers that have produced nothing for a while and tell
    the lead once. Returns the names newly flagged."""
    live = _LIVE.get(swarm_id)
    if live is None or live.inline:
        return []
    now = time.monotonic()
    flagged: list[str] = []
    for name, run in list(live.runs.items()):
        events = getattr(run, 'events', None) or []
        size = len(events) + (len(str(events[-1].get('content') or '')) if events else 0)
        seen, since = live.activity.get(name, (0, now))
        if size != seen:
            live.activity[name] = (size, now)
            continue
        if now - since >= stall_seconds():
            flagged.append(name)
            live.activity[name] = (size, now + 10 ** 9)  # told once
    if not flagged:
        return []
    swarm = await SwarmRun.get(swarm_id)
    if swarm is None:
        return []
    for name in flagged:
        worker = await SwarmWorker.find_one({'swarm_id': swarm_id, 'name': name})
        if worker is not None:
            await SwarmWorker.update_one({'id': worker.id}, {'stalled': True})
    minutes = max(1, round(stall_seconds() / 60))
    await _note(swarm, f'{", ".join(flagged)} produced nothing for {minutes} minute(s) and may be stuck.')
    await _advance(swarm_id)
    return flagged


# ---------------------------------------------------------------------------
# Steering: stop, message, merge
# ---------------------------------------------------------------------------

async def find_worker(swarm_id: str, name: str) -> SwarmWorker | None:
    name = str(name or '').strip()
    if not name:
        return None
    row = await SwarmWorker.find_one({'swarm_id': str(swarm_id), 'name': name})
    if row is None:
        row = await SwarmWorker.find_one({'swarm_id': str(swarm_id), 'name': slug(name, '')})
    return row


async def stop_worker(user_key: str, swarm_id: str, name: str) -> SwarmWorker:
    swarm = await get_swarm(user_key, swarm_id)
    worker = await find_worker(swarm_id, name) if swarm is not None else None
    if worker is None:
        raise SwarmError('worker_not_found', f'No worker called "{name}" in that swarm.')
    live = _LIVE.get(str(swarm_id))
    if worker.status == 'queued':
        await _end_worker(worker, 'stopped', error='Stopped before it started.')
        await _advance(str(swarm_id))
    elif worker.status in ('running', 'reviewing'):
        handle = (live.runs.get(worker.name) if live else None)
        task = live.tasks.get(worker.name) if live else None
        stopped = False
        if handle is not None and getattr(handle, 'stop', None):
            stopped = bool(handle.stop())
        elif task is not None:
            stopped = bool(task.cancel())
        if not stopped:
            # Nothing is running it in this process (after a restart).
            if live is not None:
                live.runs.pop(worker.name, None)
            await _end_worker(worker, 'stopped', error='Stopped.')
            await _advance(str(swarm_id))
    return worker


async def stop_swarm(user_key: str, swarm_id: str, *, reason: str = 'Stopped.') -> SwarmRun:
    swarm = await get_swarm(user_key, swarm_id)
    if swarm is None:
        raise SwarmError('swarm_not_found', 'No such swarm.')
    if swarm.status == 'running':
        workers = await workers_of(str(swarm_id))
        # Told now, so the lead is not woken by a report of its own stop.
        await SwarmRun.update_one({'id': swarm.id}, {
            'status': 'stopped', 'stop_reason': reason[:300], 'finished_at': now_iso(),
            'reported': [w.name for w in workers],
        })
        for worker in workers:
            if worker.status not in ENDED:
                try:
                    await stop_worker(user_key, swarm_id, worker.name)
                except SwarmError:
                    pass
        await _advance(str(swarm_id))
    return await SwarmRun.get(str(swarm_id)) or swarm


async def stop_all(user_key: str, agent_id: str = '') -> int:
    """Stop every running swarm of a person (Stop all), or only those one agent leads."""
    count = 0
    for swarm in await SwarmRun.find({'user_id': str(user_key), 'status': 'running'}) or []:
        if agent_id and str(swarm.agent_id) != str(agent_id):
            continue
        await stop_swarm(user_key, str(swarm.id), reason='Stopped with Stop all.')
        count += 1
    return count


async def send_message(user_key: str, swarm_id: str, to: str, text: str, *, sender: str = '') -> str:
    """A message to a worker from the lead (``sender`` '') or a teammate, or
    from a worker to the lead (``to`` 'lead'). Returns what happened."""
    try:
        return await _send_message(user_key, swarm_id, to, text, sender=sender)
    except SwarmError:
        if str(swarm_id) in _LIVE:
            # A finished swarm that was woken for this message settles again.
            _spawn(_advance(str(swarm_id)))
        raise


async def _send_message(user_key: str, swarm_id: str, to: str, text: str, *, sender: str = '') -> str:
    swarm = await get_swarm(user_key, swarm_id)
    if swarm is None:
        raise SwarmError('swarm_not_found', 'No such swarm.')
    text = str(text or '').strip()
    if not text:
        raise SwarmError('message_required', 'Write the message to send.')
    text = text[:BRIEF_MAX_CHARS]
    live = _LIVE.get(str(swarm_id))
    if live is None and not sender and swarm.status == 'done' and swarm.session_id and not swarm.run_id:
        live = await _revive(swarm)
    if str(to or '').strip().lower() in ('lead', 'leader', swarm.agent_name.lower()) and sender:
        if live is None or swarm.status != 'running' or not swarm.session_id:
            raise SwarmError('not_running', 'The swarm has ended; put it in your result instead.')
        key = (sender, 'lead')
        if live.pairs.get(key, 0) >= MAX_MESSAGES_PER_PAIR:
            raise SwarmError('too_many_messages', 'You have messaged the lead enough. Put the rest in your result.')
        live.pairs[key] = live.pairs.get(key, 0) + 1
        from cognitrix.sessions.agent_chats import REPORT_PREFIX

        body = (f'{REPORT_PREFIX}{sender} (a worker in your swarm "{swarm.title}") says:\n\n{text}\n\n'
                'Answer it with Swarm (action "message") only if it needs an answer.')
        _spawn(_deliver(swarm, body))
        return 'Sent to the lead.'
    worker = await find_worker(swarm_id, to)
    if worker is None or worker.name == sender:
        raise SwarmError('worker_not_found', f'No teammate called "{to}". Swarm board lists who is in the swarm.')
    if live is None or swarm.status != 'running':
        raise SwarmError('not_running', 'That swarm is not running any more. Start a new one for more work.')
    if worker.received >= MAX_MESSAGES_PER_WORKER:
        raise SwarmError('too_many_messages', f'"{worker.name}" has been sent {MAX_MESSAGES_PER_WORKER} messages already.')
    if sender:
        key = (sender, worker.name)
        if live.pairs.get(key, 0) >= MAX_MESSAGES_PER_PAIR:
            raise SwarmError('too_many_messages', f'You have messaged "{worker.name}" enough. Carry on with your own job.')
        live.pairs[key] = live.pairs.get(key, 0) + 1
    who = f'{sender} (a teammate in the swarm)' if sender else f'{swarm.agent_name} (the lead)'
    body = (f'{who} says:\n\n{text}\n\nTake it into account. Do not reply only to thank or confirm; '
            'your result goes to the lead when you finish.')
    await SwarmWorker.update_one({'id': worker.id}, {'received': worker.received + 1})
    if live.inline:
        raise SwarmError('not_supported', 'Workers inside a task run cannot be messaged.')
    run = live.runs.get(worker.name)
    if worker.status in ('running', 'reviewing') and run is not None and run.running:
        try:
            if run.steer(body, source='agent') is not None:
                return f'"{worker.name}" is working and will read it between steps.'
        except Exception:
            logger.debug('Could not steer worker %s', worker.name, exc_info=True)
    if worker.status == 'queued':
        await SwarmWorker.update_one({'id': worker.id}, {'brief': f'{worker.brief}\n\nAdded later by {who}:\n{text}'[:BRIEF_MAX_CHARS + 2000]})
        return f'"{worker.name}" has not started yet; the message was added to its brief.'
    if worker.status in ('done', 'failed', 'stopped') and worker.chat_id:
        async with live.lock:
            await SwarmWorker.update_one({'id': worker.id}, {
                'status': 'running', 'finished_at': '', 'error': '', 'stalled': False})
            worker.status = 'running'
            reported = [n for n in (swarm.reported or []) if n != worker.name]
            await SwarmRun.update_one({'id': swarm.id}, {'reported': reported})
            if not await _continue_chat(live, swarm, worker, body + '\n\nReply with your complete updated result '
                                                                      '(begin with "Summary:").'):
                await _end_worker(worker, 'failed', error='Its chat could not be reopened.')
                raise SwarmError('worker_gone', f'"{worker.name}" could not be reopened.')
        return f'"{worker.name}" had finished; it is working again and its new result will be reported.'
    raise SwarmError('worker_busy', f'"{worker.name}" cannot take a message right now. Try again shortly.')


async def _revive(swarm: SwarmRun) -> _Live | None:
    """A finished swarm goes back to work when its lead sends a worker a message."""
    lead = await _lead_agent(swarm)
    if lead is None:
        return None
    swarm_id = str(swarm.id)
    live = _LIVE[swarm_id] = _Live(swarm_id=swarm_id, user_key=swarm.user_id, lead=lead)
    await SwarmRun.update_one({'id': swarm.id}, {'status': 'running', 'finished_at': ''})
    swarm.status = 'running'
    live.watchdog = _spawn(_watch(swarm_id))
    live.watchdog._swarm_watchdog = True  # type: ignore[attr-defined]
    return live


async def read_result(user_key: str, swarm_id: str, name: str, *, file: str = '', offset: int = 0) -> str:
    """A worker's full result (or another file it left), a page at a time."""
    swarm = await get_swarm(user_key, swarm_id)
    worker = await find_worker(swarm_id, name) if swarm is not None else None
    if swarm is None or worker is None:
        raise SwarmError('worker_not_found', f'No worker called "{name}" in that swarm.')
    folder = worker_folder(swarm, worker).resolve()
    if file:
        target = (folder / file).resolve()
        if folder not in target.parents or not target.is_file():
            raise SwarmError('file_not_found', f'"{file}" is not one of the files of "{worker.name}".')
    else:
        if worker.status != 'done' or not worker.result_path:
            state = worker.error or worker.status
            raise SwarmError('no_result', f'"{worker.name}" has no result yet ({state}).')
        target = Path(worker.result_path)
    offset = max(0, int(offset or 0))
    text = await asyncio.to_thread(_read_text, str(target), RESULT_READ_CHARS + 1, offset)
    if not text and offset == 0:
        raise SwarmError('no_result', f'The result of "{worker.name}" could not be read.')
    more = len(text) > RESULT_READ_CHARS
    text = text[:RESULT_READ_CHARS]
    files = await asyncio.to_thread(_list_files, folder)
    tail = []
    if more:
        tail.append(f'(more follows: ask again with offset {offset + RESULT_READ_CHARS})')
    if files and not file:
        tail.append('Other files it left: ' + ', '.join(files[:20]))
    return text + ('\n\n' + '\n'.join(tail) if tail else '')


def _list_files(folder: Path) -> list[str]:
    try:
        return sorted(str(p.relative_to(folder)).replace('\\', '/') for p in folder.rglob('*')
                      if p.is_file() and p.name != 'result.md')
    except OSError:
        return []


async def merge_worker(user_key: str, swarm_id: str, name: str) -> tuple[bool, str]:
    """Bring a worker's changes from its own copy into the project."""
    swarm = await get_swarm(user_key, swarm_id)
    worker = await find_worker(swarm_id, name) if swarm is not None else None
    if swarm is None or worker is None:
        raise SwarmError('worker_not_found', f'No worker called "{name}" in that swarm.')
    tree = dict(worker.worktree or {})
    if not tree:
        raise SwarmError('nothing_to_merge', f'"{worker.name}" worked in the shared folders; there is nothing to merge.')
    if tree.get('merged'):
        return True, 'Already merged.'
    if worker.status not in ENDED:
        raise SwarmError('worker_busy', f'"{worker.name}" is still working. Merge when it has finished.')
    ok, message = await asyncio.to_thread(worktrees.merge, tree)
    if ok:
        await asyncio.to_thread(worktrees.remove, tree)
        tree['merged'] = True
        await SwarmWorker.update_one({'id': worker.id}, {'worktree': tree})
    return ok, message


async def discard_copies(user_key: str, swarm_id: str) -> int:
    """Remove the unmerged copies a swarm's workers left."""
    swarm = await get_swarm(user_key, swarm_id)
    if swarm is None:
        raise SwarmError('swarm_not_found', 'No such swarm.')
    count = 0
    for worker in await workers_of(str(swarm_id)):
        tree = dict(worker.worktree or {})
        if tree and not tree.get('merged') and worker.status in ENDED:
            await asyncio.to_thread(worktrees.remove, tree)
            await SwarmWorker.update_one({'id': worker.id}, {'worktree': {}})
            count += 1
    return count


# ---------------------------------------------------------------------------
# For pages and tools
# ---------------------------------------------------------------------------

def public_worker(worker: SwarmWorker, live: _Live | None = None) -> dict[str, Any]:
    tree = worker.worktree or {}
    run = live.runs.get(worker.name) if live else None
    return {
        'id': str(worker.id), 'name': worker.name, 'role': worker.role,
        'agent_id': worker.agent_id, 'agent_name': worker.agent_name,
        'model': (worker.model or {}).get('model') or '',
        'brief': worker.brief[:600], 'depends_on': list(worker.depends_on or []),
        'may_edit': worker.may_edit, 'status': worker.status, 'stalled': bool(worker.stalled),
        'chat_id': worker.chat_id, 'summary': worker.summary, 'error': worker.error,
        'tokens': live.spent.get(worker.name, worker.tokens) if live else worker.tokens,
        'review': (worker.review or {}).get('verdict') or '',
        'changed': len(tree.get('changed') or []), 'branch': tree.get('branch') or '',
        'merged': bool(tree.get('merged')), 'added_by': worker.added_by,
        'last_step': _last_step(run),
        'started_at': worker.started_at, 'finished_at': worker.finished_at,
    }


def _last_step(run: Any) -> str:
    """What a running worker is doing, from its newest tool call."""
    for event in reversed(getattr(run, 'events', None) or []):
        if event.get('type') == 'tool' and event.get('tool_name'):
            return str(event['tool_name'])[:60]
    return ''


async def public_swarm(swarm: SwarmRun) -> dict[str, Any]:
    workers = await workers_of(str(swarm.id))
    live = _LIVE.get(str(swarm.id))
    return {
        'id': str(swarm.id), 'session_id': swarm.session_id, 'agent_id': swarm.agent_id,
        'agent_name': swarm.agent_name, 'title': swarm.title, 'mode': swarm.mode, 'status': swarm.status,
        'stop_reason': swarm.stop_reason, 'review': swarm.review,
        'limits': dict(swarm.limits or {}), 'judge': dict(swarm.judge or {}),
        'ledger': ledger(swarm, workers), 'task_id': swarm.task_id, 'run_id': swarm.run_id,
        'started_at': swarm.started_at, 'finished_at': swarm.finished_at,
        'workers': [public_worker(w, live) for w in workers],
    }


def board_text(swarm: SwarmRun, workers: list[SwarmWorker], *, me: str = '') -> str:
    """The board as the agents read it."""
    state = ledger(swarm, workers)
    live = _LIVE.get(str(swarm.id))
    lines = [f'Swarm "{swarm.title}" (swarm id {swarm.id}), {swarm.status}: {_ledger_line(state)}.']
    for w in workers:
        bits = [w.status + (' (stalled?)' if w.stalled and w.status == 'running' else '')]
        if w.role:
            bits.append(f'role {w.role}')
        if w.depends_on:
            bits.append('after ' + ', '.join(w.depends_on))
        step = _last_step(live.runs.get(w.name)) if live else ''
        if step and w.status == 'running':
            bits.append(f'last step: {step}')
        if w.status == 'failed' and w.error:
            bits.append(w.error[:160])
        mark = ' (you)' if me and w.name == me else ''
        lines.append(f'- {w.name}{mark}: {"; ".join(bits)}. Job: {w.brief[:160].strip()}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# After a restart
# ---------------------------------------------------------------------------

async def recover() -> int:
    """Pick up swarms that were running when the engine stopped: workers that
    were mid-turn carry on in their own chats, waiting ones start as usual.
    Returns how many swarms were picked up."""
    count = 0
    for swarm in await SwarmRun.find({'status': 'running'}) or []:
        swarm_id = str(swarm.id)
        if swarm_id in _LIVE:
            continue
        workers = await workers_of(swarm_id)
        if not swarm.session_id or swarm.run_id:
            # Inside a task run: the step is run again and starts a new swarm
            # that reuses what finished (see _reuse_finished).
            for worker in workers:
                if worker.status not in ENDED:
                    await _end_worker(worker, 'stopped', error='The engine restarted.')
            await SwarmRun.update_one({'id': swarm.id}, {
                'status': 'stopped', 'stop_reason': 'The engine restarted.', 'finished_at': now_iso()})
            continue
        lead = await _lead_agent(swarm)
        if lead is None:
            await SwarmRun.update_one({'id': swarm.id}, {
                'status': 'stopped', 'stop_reason': 'Its lead agent no longer exists.', 'finished_at': now_iso()})
            continue
        live = _LIVE[swarm_id] = _Live(swarm_id=swarm_id, user_key=swarm.user_id, lead=lead)
        for worker in workers:
            if worker.status not in ('running', 'reviewing'):
                continue
            live.spent[worker.name] = worker.tokens
            resumed = bool(worker.chat_id) and await _continue_chat(
                live, swarm, worker,
                'The app restarted while you were working. Carry on with the job above from where you were '
                'and finish with your complete result (begin with "Summary:").')
            if not resumed:
                # It never got a chat: start it afresh.
                await SwarmWorker.update_one({'id': worker.id}, {'status': 'queued'})
        live.watchdog = _spawn(_watch(swarm_id))
        live.watchdog._swarm_watchdog = True  # type: ignore[attr-defined]
        await _advance(swarm_id)
        count += 1
    return count
