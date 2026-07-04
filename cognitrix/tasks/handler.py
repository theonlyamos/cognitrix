"""Multi-step chat handler: detection + a thin wrapper over the orchestrator.

A multi-step chat message becomes a real Task executed by the task
orchestrator (cognitrix.tasks.orchestrator), so chat-born work gets the same
run history, live monitoring, per-step sessions and named agents as tasks
started from the UI. Only the synthesis (plus a link to the run page) is
posted back into the chat conversation.
"""

import logging

from rich.console import Console

from cognitrix.agents.base import Agent
from cognitrix.providers.base import LLM
from cognitrix.sessions.base import Session

# Kept for the TUI, which temporarily redirects this console while a
# multi-step task runs (cli/tui.py reads handler.console). The wrapper itself
# no longer prints through it.
console = Console()
logger = logging.getLogger('cognitrix.log')


def is_multi_step_task(query: str) -> bool:
    """Detect if a query genuinely requires multi-step planning.

    Deliberately conservative to avoid hijacking ordinary single-task chats
    (e.g. "find me a restaurant") into the expensive planner. A query is
    multi-step only if it either explicitly sequences work or names two or
    more distinct task actions.
    """
    import re

    q = query.lower()

    # Explicit sequencing / multi-step language.
    explicit = [
        'then ', 'and then', 'after that', 'step by step', 'step-by-step',
        'first ', 'next,', 'next ', 'finally', 'followed by', 'once you',
    ]
    if any(e in q for e in explicit):
        return True

    # A numbered list of steps.
    if re.search(r'(?m)^\s*\d+[.)]\s', query):
        return True

    # Two or more distinct task verbs → likely more than one task.
    task_verbs = [
        'book', 'reserve', 'schedule', 'organize', 'plan', 'research',
        'find', 'create', 'build', 'write', 'analyze', 'compare', 'gather',
    ]
    hits = sum(1 for v in task_verbs if re.search(rf'\b{v}\b', q))
    return hits >= 2


async def handle_multi_step_task(
    query: str,
    agent: Agent,
    session: Session,
    llm: LLM,
    stream: bool = False,
    interface: str = 'cli',
    on_task_created=None,
) -> str:
    """Run a multi-step chat message as a real Task through the orchestrator.

    Returns the synthesis text (with a link to the run page) and persists it
    into the chat session. Failures are caught: the user gets the link either
    way, and the run page carries the error detail.

    ``llm`` and ``stream`` are unused but kept for call-site compatibility.
    ``on_task_created`` (optional async callable, receives the task id) lets
    streaming callers surface the run link immediately, before the run blocks
    them for its duration.
    """
    from cognitrix.tasks import orchestrator
    from cognitrix.tasks.base import Task

    title = (query.strip().splitlines() or ['Multi-step task'])[0][:60]
    task = Task(title=title or 'Multi-step task', description=query, assigned_agents=[agent.id])
    await task.save()

    if on_task_created is not None:
        try:
            await on_task_created(task.id)
        except Exception:
            logger.exception("on_task_created callback failed")

    # CLI-ish callers keep console approval prompts; web/ws deny risky tools
    # by policy (clear error, no stdin hazard in server processes).
    step_interface = 'task' if interface in ('cli', 'task') else 'web'
    link = f"[View task run](/tasks/{task.id})"
    try:
        run = await orchestrator.run(task, interface=step_interface)
        result = (run.result or '').strip() if run else ''
        text = f"{result}\n\n{link}" if result else f"Task finished.\n\n{link}"
    except Exception as exc:
        logger.exception("Multi-step task %s failed", task.id)
        text = f"Multi-step task failed: {exc}\n\n{link}"

    session.update_history({'role': 'assistant', 'name': agent.name, 'type': 'text', 'content': text})
    await session.save()
    return text
