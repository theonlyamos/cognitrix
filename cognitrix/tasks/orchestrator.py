"""The task execution engine: plan → assign → execute → gate → synthesize.

One engine for every entry point (▶ Run via Celery, autostart, chat
multi-step). A run is recorded as a TaskRun whose ``plan`` snapshot tracks
per-step status; each step executes in its own Session (``run_id`` +
``step_index``), so the UI can show who did what, live.

Persistence rules (load-bearing):
- TaskRun is instance-saved ONCE at creation. Every later write is a partial
  ``TaskRun.update_one`` — ``Model.save()`` writes the full row and would
  clobber a concurrently written 'cancelling' status from the cancel endpoint.
- Run status writes re-read the stored status first and never downgrade
  ``cancelling`` back to ``running``.
- Step failures raise: the exception path persists FAILED and, on the Celery
  path, makes the job fail so the postrun handler can't overwrite the status.
"""

import asyncio
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING, Any

from cognitrix.agents.base import Agent
from cognitrix.agents.evaluator import Evaluator
from cognitrix.sessions.base import Session
from cognitrix.tasks.run import TaskRun, TaskRunStatus

if TYPE_CHECKING:
    from cognitrix.tasks.base import Task

logger = logging.getLogger('cognitrix.log')

STEP_TIMEOUT = int(os.getenv('COGNITRIX_STEP_TIMEOUT', '600'))
GATE_THRESHOLD = float(os.getenv('COGNITRIX_GATE_THRESHOLD', '7'))
MAX_PARALLEL_STEPS = int(os.getenv('COGNITRIX_MAX_PARALLEL_STEPS', '3'))
MAX_PLAN_STEPS = 10
RESULT_TRUNCATE = 8000
EMPTY_TURN_BACKOFF = 10  # seconds before retrying an empty agent turn


def _now() -> str:
    return datetime.now().strftime("%a %b %d %Y %H:%M:%S")


class StepFailure(Exception):
    """A plan step failed terminally (empty turns, gate rejection)."""


class RunCancelled(Exception):
    """The run was cancelled (observed 'cancelling' at a checkpoint)."""


# ---------------------------------------------------------------- plan build

def _new_step(index: int, title: str, description: str, dependencies: list[int],
              expected_output: str = '', verification_criteria: str = '',
              agent_name: str = '') -> dict[str, Any]:
    return {
        'index': index,
        'title': (title or '').strip()[:120] or f'Step {index + 1}',
        'description': description,
        'expected_output': expected_output or '',
        'verification_criteria': verification_criteria or '',
        'agent_name': agent_name or '',
        'dependencies': dependencies,
        'status': 'pending',
        'attempts': 0,
        'result': None,
        'gate': None,
    }


def _template_plan(task: 'Task') -> list[dict[str, Any]]:
    """Snapshot authored step_instructions as a sequential chain."""
    si = task.step_instructions or {}
    keys = sorted(si, key=lambda k: int(k))
    return [
        _new_step(i, str(si[k].get('step', '')), str(si[k].get('step', '')),
                  [i - 1] if i > 0 else [])
        for i, k in enumerate(keys)
    ]


async def _planner_plan(task: 'Task', roster: list[Agent], leader: Agent) -> list[dict[str, Any]]:
    """Generate a plan from the description via StructuredPlanner (never raises)."""
    from cognitrix.planning.structured_planner import StructuredPlanner

    tools = list({t.name: t for a in roster for t in (a.tools or [])}.values())
    plan = await StructuredPlanner(leader.llm).create_plan(task.description, roster, tools)
    steps = list(plan.steps or [])
    if not steps or len(steps) > MAX_PLAN_STEPS or not all((s.title or '').strip() for s in steps):
        logger.warning("Task %s: planner output invalid, falling back to single step", task.id)
        return [_new_step(0, task.title or 'Complete the task', task.description, [])]

    roster_names = {a.name.lower(): a.name for a in roster}
    number_to_index = {s.step_number: i for i, s in enumerate(steps)}
    out = []
    for i, s in enumerate(steps):
        deps = [number_to_index[d] for d in (s.dependencies or [])
                if d in number_to_index and number_to_index[d] != i]
        assigned = roster_names.get((s.assigned_agent or '').lower(), '')
        out.append(_new_step(i, s.title, s.description, deps,
                             getattr(s, 'expected_output', '') or '',
                             getattr(s, 'verification_criteria', '') or '',
                             assigned))
    return out


# ---------------------------------------------------------------- assignment

async def _collect_generation(agent: Agent, prompt: str) -> str:
    """One plain LLM turn (no tools/session) via Agent.generate."""
    chunks: list[str] = []
    last = None
    async for response in agent.generate(prompt):
        last = response
        chunk = getattr(response, 'current_chunk', None)
        if chunk:
            chunks.append(chunk)
    full = getattr(last, 'llm_response', '') or ''
    return full if len(full) >= len(''.join(chunks)) else ''.join(chunks)


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM reply (fenced or raw)."""
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    brace = re.search(r'\{.*\}', text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


async def _assign_agents(plan: list[dict], roster: list[Agent], leader: Agent) -> None:
    """Fill agent_name for every step. One LLM call total, not one per step."""
    by_lower = {a.name.lower(): a.name for a in roster}
    unassigned = [s for s in plan if not s['agent_name']]
    if not unassigned:
        return
    if len(roster) == 1:
        for s in plan:
            s['agent_name'] = s['agent_name'] or roster[0].name
        return

    roster_desc = '\n'.join(
        f"- {a.name}: {(a.system_prompt or '')[:200].strip()} (tools: {', '.join(t.name for t in (a.tools or [])) or 'none'})"
        for a in roster
    )
    steps_desc = json.dumps(
        [{'index': s['index'], 'title': s['title'], 'description': s['description'][:300]} for s in unassigned]
    )
    prompt = (
        "Assign the best-fit team member to each step. Team:\n"
        f"{roster_desc}\n\nSteps (JSON):\n{steps_desc}\n\n"
        'Reply with ONLY a JSON object mapping step index to member name, e.g. {"0": "Backend Engineer", "1": "QA Engineer"}.'
    )
    try:
        reply = await _collect_generation(leader, prompt)
        mapping = _extract_json(reply) or {}
    except Exception:
        logger.exception("Assignment call failed; defaulting to leader")
        mapping = {}

    for s in plan:
        if s['agent_name']:
            continue
        raw = str(mapping.get(str(s['index']), '') or '')
        s['agent_name'] = by_lower.get(raw.lower().strip(), leader.name)


# ---------------------------------------------------------------- scheduling

def _dependency_batches(plan: list[dict]) -> list[list[int]]:
    """Kahn's algorithm → batches of step indexes whose deps are satisfied.
    A cycle degrades to sequential order (never drops steps)."""
    pending = {s['index']: set(d for d in s['dependencies'] if d != s['index']) for s in plan}
    batches: list[list[int]] = []
    done: set[int] = set()
    while pending:
        ready = sorted(i for i, deps in pending.items() if deps <= done)
        if not ready:
            logger.warning("Dependency cycle detected; falling back to sequential order")
            remaining = sorted(pending)
            batches.extend([[i] for i in remaining])
            break
        batches.append(ready)
        done.update(ready)
        for i in ready:
            del pending[i]
    return batches


# ---------------------------------------------------------------- execution

def _summarize_recent_activity(session: Session, window: int = 24) -> str:
    """Best-effort summary of a turn from the session tail: last assistant
    text, else a tally of invoked tools. (Copied from tasks/handler.py — the
    handler's copy is deleted in the chat-unification phase.)"""
    msgs = session.chat[-window:] if session and session.chat else []
    for m in reversed(msgs):
        if str(m.get('role', '')).lower() == 'assistant' and m.get('type') == 'text':
            content = str(m.get('content') or '').strip()
            if content:
                return content
    tools = [tc.get('name') for m in msgs for tc in (m.get('tool_calls') or []) if tc.get('name')]
    if tools:
        tally = ', '.join(f"{n} (x{c})" if c > 1 else n for n, c in Counter(tools).items())
        return f"Completed {len(tools)} tool call(s): {tally}."
    return ""


async def _run_agent_turn(session: Session, agent: Agent, prompt: str, interface: str) -> str:
    """One session turn; returns the captured answer text ('' on a dead turn)."""
    captured = ''

    async def capture(payload=None, *args, **kwargs):
        nonlocal captured
        content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
        if content:
            captured += content

    await session(prompt, agent, interface=interface, stream=True, output=capture, wsquery={})
    answer = captured.strip()
    if 'Streaming error:' in answer:
        # Provider-level failures are forwarded as display chunks (so chat
        # users see them) but nothing is persisted — this is a dead turn, not
        # an answer. Returning '' routes it into the retry/fail path.
        logger.warning("Turn for agent %s hit a provider error: %.120s", agent.name, answer)
        return ''
    if not answer:
        answer = _summarize_recent_activity(session).strip()
    return answer


def _parse_finalscore(text: str) -> tuple[float | None, list[str]]:
    """Extract (score, suggestions) from an evaluator reply."""
    data = _extract_json(text or '')
    if not data:
        return None, []
    raw = data.get('finalscore')
    suggestions = [str(s) for s in (data.get('suggestions') or []) if str(s).strip()]
    if raw is None:
        return None, suggestions
    m = re.search(r'\d+(?:\.\d+)?', str(raw))
    return (float(m.group(0)) if m else None), suggestions


async def _gate(session: Session, agent: Agent, step: dict, answer: str, interface: str) -> tuple[bool, list[str]]:
    """Evaluator-as-gate. Returns (passed, suggestions). Evaluator infra
    failure (empty/unparseable twice) passes the step marked 'unverified' —
    the gate exists to catch bad agent output, not evaluator downtime."""
    evaluator = Evaluator(llm=agent.llm)
    eval_prompt = f"Task: {step['description']}\n\nAgent Response:\n{answer}"
    if step.get('verification_criteria'):
        eval_prompt += f"\n\nVerification criteria:\n{step['verification_criteria']}"

    for _attempt in range(2):
        text = await _run_agent_turn(session, evaluator, eval_prompt, interface)
        score, suggestions = _parse_finalscore(text)
        if score is not None:
            if score >= GATE_THRESHOLD:
                step['gate'] = 'passed'
                return True, []
            return False, suggestions
    logger.warning("Step %s: evaluator unusable twice — passing unverified", step['index'])
    step['gate'] = 'unverified'
    return True, []


def _step_prompt(task: 'Task', step: dict, dep_results: dict[int, str]) -> str:
    parts = [
        f"You are completing one step of the task: {task.title}",
        f"Task description: {task.description}",
        f"\nYour step (#{step['index'] + 1}): {step['title']}\n{step['description']}",
    ]
    if step.get('expected_output'):
        parts.append(f"Expected output: {step['expected_output']}")
    deps = [d for d in step['dependencies'] if dep_results.get(d)]
    if deps:
        parts.append("\nResults of prerequisite steps:")
        for d in deps:
            parts.append(f"--- Step #{d + 1} result ---\n{dep_results[d][:4000]}")
    parts.append("\nComplete ONLY your step and reply with its result.")
    return '\n'.join(parts)


async def _execute_step(task: 'Task', run: TaskRun, step: dict, agent: Agent,
                        dep_results: dict[int, str], interface: str) -> str:
    """Run one plan step in its own session. Raises StepFailure terminally."""
    session = Session(task_id=task.id, run_id=run.id, step_index=step['index'],
                      step_title=step['title'], agent_id=agent.id)
    session.started_at = _now()
    await session.save()

    prompt = _step_prompt(task, step, dep_results)
    try:
        # Empty turns are how provider failures (rate limits) surface — the
        # provider swallows stream errors by design. Retry once with backoff.
        answer = ''
        for attempt in range(2):
            step['attempts'] += 1
            answer = await _run_agent_turn(session, agent, prompt, interface)
            if answer:
                break
            logger.warning("Task %s step %s: empty turn (attempt %s)", task.id, step['index'], attempt + 1)
            if attempt == 0:
                await asyncio.sleep(EMPTY_TURN_BACKOFF)
        if not answer:
            raise StepFailure(f"Step #{step['index'] + 1} produced no answer after retry")

        passed, suggestions = await _gate(session, agent, step, answer, interface)
        if not passed:
            if await _cancel_requested(run):
                raise RunCancelled()
            retry_prompt = (
                f"{prompt}\n\nA reviewer scored your previous attempt below the quality bar."
                + ("\nAddress these suggestions:\n- " + "\n- ".join(suggestions[:6]) if suggestions else "")
                + f"\n\nYour previous attempt:\n{answer[:4000]}"
            )
            step['attempts'] += 1
            retry_answer = await _run_agent_turn(session, agent, retry_prompt, interface)
            if not retry_answer:
                raise StepFailure(f"Step #{step['index'] + 1} empty on gate retry")
            passed, _ = await _gate(session, agent, step, retry_answer, interface)
            if not passed:
                raise StepFailure(f"Step #{step['index'] + 1} below gate threshold after retry")
            answer = retry_answer
        return answer
    finally:
        session.completed_at = _now()
        try:
            await session.save()
        except Exception:
            logger.exception("Could not stamp step session %s", session.id)


async def _run_step_guarded(task: 'Task', run: TaskRun, step: dict, agent: Agent,
                            dep_results: dict[int, str], interface: str,
                            semaphore: asyncio.Semaphore) -> tuple[dict, str, str]:
    """Run one step under the concurrency slot with a hard timeout.

    Returns (step, outcome, payload) where outcome is 'done' | 'failed' |
    'cancelled'. The timeout wraps only the step BODY — a step queued behind
    the semaphore must not burn its budget waiting for a slot. wait_for
    cancellation still runs _execute_step's finally, so the partial transcript
    is stamped and saved.
    """
    async with semaphore:
        if await _cancel_requested(run):
            return step, 'cancelled', ''
        try:
            result = await asyncio.wait_for(
                _execute_step(task, run, step, agent, dep_results, interface),
                STEP_TIMEOUT,
            )
            return step, 'done', result
        except RunCancelled:
            return step, 'cancelled', ''
        except asyncio.TimeoutError:
            return step, 'failed', (
                f"Step #{step['index'] + 1} timed out after {STEP_TIMEOUT}s. "
                "In-flight tool side effects (threads/subprocesses) cannot be "
                "killed and may still land."
            )
        except StepFailure as exc:
            return step, 'failed', str(exc)
        except Exception as exc:  # tool/session crash — terminal for the step
            logger.exception("Task %s step %s crashed", task.id, step['index'])
            return step, 'failed', f"Step #{step['index'] + 1} crashed: {exc}"


# ---------------------------------------------------------------- run status

async def _save_plan(run: TaskRun) -> None:
    await TaskRun.update_one({'id': run.id}, {'plan': run.plan})


async def _cancel_requested(run: TaskRun) -> bool:
    """Fresh DB read — the cancel endpoint may live in another process."""
    fresh = await TaskRun.get(run.id)
    return bool(fresh and fresh.status == TaskRunStatus.CANCELLING)


async def _set_run_status(run: TaskRun, status: TaskRunStatus, *,
                          error: str | None = None, result: str | None = None,
                          completed: bool = False) -> None:
    """Compare-and-set style status write: never downgrade a concurrently
    written 'cancelling' back to 'running', and always use partial updates."""
    fresh = await TaskRun.get(run.id)
    current = fresh.status if fresh else run.status
    if current == TaskRunStatus.CANCELLING and status == TaskRunStatus.RUNNING:
        run.status = TaskRunStatus.CANCELLING
        return
    updates: dict[str, Any] = {'status': status.value}
    if error is not None:
        updates['error'] = error
    if result is not None:
        updates['result'] = result
    if completed:
        updates['completed_at'] = _now()
    await TaskRun.update_one({'id': run.id}, updates)
    run.status = status


def _cancel_pending(plan: list[dict]) -> None:
    for s in plan:
        if s['status'] == 'pending':
            s['status'] = 'cancelled'


def _copy_plan_for_resume(plan: list[dict]) -> list[dict]:
    """Deep-copy a terminal run's plan: done steps keep status + result (they
    feed downstream dependency prompts), everything else resets to pending."""
    import copy
    new = copy.deepcopy(plan)
    for s in new:
        if s['status'] != 'done':
            s.update(status='pending', attempts=0, result=None, gate=None)
    return new


# ---------------------------------------------------------------- synthesis

async def _synthesize(task: 'Task', run: TaskRun, leader: Agent, interface: str) -> str:
    session = Session(task_id=task.id, run_id=run.id, step_index=None,
                      step_title='Synthesis', agent_id=leader.id)
    session.started_at = _now()
    await session.save()
    parts = [
        f"All steps of the task '{task.title}' are complete.",
        f"Task description: {task.description}",
        "\nStep results:",
    ]
    for s in run.plan:
        if s.get('result'):
            parts.append(f"--- {s['title']} ---\n{s['result'][:4000]}")
    parts.append("\nWrite the final deliverable/answer for the task, synthesizing the step results. Reply with the deliverable only.")
    try:
        text = await _run_agent_turn(session, leader, '\n'.join(parts), interface)
    finally:
        session.completed_at = _now()
        try:
            await session.save()
        except Exception:
            logger.exception("Could not stamp synthesis session %s", session.id)
    if not text:
        text = '\n\n'.join(f"## {s['title']}\n{s['result']}" for s in run.plan if s.get('result'))
    return text


# ------------------------------------------------------------------- entry

async def _resolve_leader(task: 'Task', roster: list[Agent]) -> Agent:
    """Team leader when set — loaded via Agent.get, NOT the Team.leader
    property (which silently replaces the agent's LLM with the provider
    default model). Falls back to the first assigned agent."""
    if task.team_id:
        from cognitrix.teams.base import Team
        team = await Team.get(task.team_id)
        if team and team.leader_id:
            leader = await Agent.get(team.leader_id)
            if leader:
                return leader
    return roster[0]


async def run(task: 'Task', resume: bool = False, interface: str = 'web') -> TaskRun | None:
    """Execute a task. Returns the TaskRun, or None when the task was
    cancelled before pickup. Raises on failure (so the Celery job fails and
    the postrun handler cannot overwrite FAILED with COMPLETED)."""
    from cognitrix.tasks.base import TaskStatus

    fresh = await type(task).get(task.id)
    if fresh is not None:
        task = fresh
    if task.status == TaskStatus.CANCELLED:
        logger.info("Task %s cancelled before pickup — skipping run", task.id)
        return None

    # Resume: copy the newest failed/cancelled run's plan; done steps keep
    # their status + stored result (feeding downstream dependency prompts),
    # everything else resets. Template drift on the task is deliberately
    # ignored — the snapshot wins.
    prev_plan: list[dict[str, Any]] | None = None
    if resume:
        existing = await TaskRun.find({'task_id': task.id})
        if any(r.status in (TaskRunStatus.RUNNING, TaskRunStatus.CANCELLING) for r in existing):
            raise ValueError(f"Task {task.id} already has an active run")
        resumable = [r for r in existing if r.status in (TaskRunStatus.FAILED, TaskRunStatus.CANCELLED)]
        resumable.sort(key=lambda r: r.json().get('created_at') or '', reverse=True)
        if resumable and resumable[0].plan:
            prev_plan = _copy_plan_for_resume(resumable[0].plan)
        else:
            logger.info("Task %s: nothing to resume — running fresh", task.id)

    roster = await task.team()
    if not roster:
        failed = TaskRun(task_id=task.id, status=TaskRunStatus.FAILED, started_at=_now(),
                         completed_at=_now(), error='no agents assigned to this task')
        await failed.save()
        task.status = TaskStatus.FAILED
        await task.save()
        raise RuntimeError(f"Task {task.id} has no agents assigned")

    leader = await _resolve_leader(task, roster)
    agents_by_name = {a.name.lower(): a for a in roster}
    agents_by_name.setdefault(leader.name.lower(), leader)

    task.status = TaskStatus.IN_PROGRESS
    # Transitional 'steps X/Y' compat — the per-run truth lives in
    # TaskRun.plan. Fresh runs clear all ticks; resumed runs tick the steps
    # the copied plan already has done.
    template_keys = sorted((task.step_instructions or {}), key=lambda k: int(k))
    for i, k in enumerate(template_keys):
        task.step_instructions[k]['done'] = bool(
            prev_plan and i < len(prev_plan) and prev_plan[i]['status'] == 'done'
        )
    await task.save()

    run_rec = TaskRun(task_id=task.id, status=TaskRunStatus.RUNNING, started_at=_now())
    await run_rec.save()  # the ONE instance save — partial updates after this

    try:
        # Plan (resume reuses the copied snapshot, assignments included)
        if prev_plan is not None:
            plan = prev_plan
        elif task.step_instructions:
            plan = _template_plan(task)
            await _assign_agents(plan, roster, leader)
        else:
            plan = await _planner_plan(task, roster, leader)
            await _assign_agents(plan, roster, leader)
        run_rec.plan = plan
        await _save_plan(run_rec)

        # Execute: dependency-ready batches, parallel within a batch. Cancel
        # is observed from the DB at every boundary; in-flight steps are
        # awaited (never killed mid-LLM-call — their results help resume).
        semaphore = asyncio.Semaphore(MAX_PARALLEL_STEPS)
        dep_results: dict[int, str] = {}
        cancelled = False
        failure_msg: str | None = None
        for batch in _dependency_batches(plan):
            if await _cancel_requested(run_rec):
                cancelled = True
                break
            runnable: list[dict] = []
            for idx in batch:
                step = plan[idx]
                if step['status'] == 'done':
                    dep_results[idx] = step.get('result') or ''
                else:
                    runnable.append(step)
            if not runnable:
                continue
            for s in runnable:
                s['status'] = 'running'
            await _save_plan(run_rec)

            outcomes = await asyncio.gather(*[
                _run_step_guarded(
                    task, run_rec, s,
                    agents_by_name.get((s['agent_name'] or '').lower(), leader),
                    dep_results, interface, semaphore,
                )
                for s in runnable
            ])

            for step, outcome, payload in outcomes:
                if outcome == 'done':
                    step['status'] = 'done'
                    step['result'] = payload[:RESULT_TRUNCATE]
                    dep_results[step['index']] = step['result']
                    if step['index'] < len(template_keys):
                        task.step_instructions[template_keys[step['index']]]['done'] = True
                elif outcome == 'cancelled':
                    step['status'] = 'cancelled'
                    cancelled = True
                else:
                    step['status'] = 'failed'
                    failure_msg = failure_msg or payload
            if template_keys:
                await task.save()
            await _save_plan(run_rec)
            if failure_msg or cancelled:
                break

        if failure_msg:
            _cancel_pending(plan)
            await _save_plan(run_rec)
            raise RuntimeError(failure_msg)

        if cancelled or await _cancel_requested(run_rec):
            # Cancellation is a normal outcome, not a failure — return without
            # raising so the Celery job succeeds and postrun (guarded to
            # IN_PROGRESS-only) leaves CANCELLED alone.
            _cancel_pending(plan)
            await _save_plan(run_rec)
            await _set_run_status(run_rec, TaskRunStatus.CANCELLED,
                                  error='cancelled by user', completed=True)
            task.status = TaskStatus.CANCELLED
            await task.save()
            logger.info("Task %s run %s cancelled", task.id, run_rec.id)
            return run_rec

        # Synthesize
        synthesis = await _synthesize(task, run_rec, leader, interface)
        await _set_run_status(run_rec, TaskRunStatus.COMPLETED, result=synthesis, completed=True)
        task.results.append(synthesis)
        task.status = TaskStatus.COMPLETED
        await task.save()
        return run_rec
    except Exception as exc:
        logger.exception("Task %s run %s failed", task.id, run_rec.id)
        _cancel_pending(run_rec.plan)
        try:
            await _save_plan(run_rec)
        except Exception:
            logger.exception("Could not persist failed plan for run %s", run_rec.id)
        await _set_run_status(run_rec, TaskRunStatus.FAILED, error=str(exc), completed=True)
        task.status = TaskStatus.FAILED
        await task.save()
        raise
