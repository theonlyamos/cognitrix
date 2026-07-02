"""Multi-step task handler with planning and verification."""

import hashlib
import logging
import os
import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from cognitrix.agents.base import Agent
from cognitrix.planning.structured_planner import StructuredPlanner
from cognitrix.providers.base import LLM
from cognitrix.sessions.base import Session
from cognitrix.tasks.tracker import StepResult, get_task_tracker

console = Console()
logger = logging.getLogger('cognitrix.log')


def _topological_fallback(workflow_steps: list[dict]) -> list[int]:
    """Order steps respecting their dependencies (Kahn's algorithm) when the
    planner's own ordering fails. Falls back to raw plan order only for the
    steps left in a dependency cycle, instead of silently ignoring deps."""
    nums = [s["step_number"] for s in workflow_steps]
    deps = {s["step_number"]: [d for d in (s.get("dependencies") or []) if d in nums] for s in workflow_steps}
    ordered: list[int] = []
    resolved: set[int] = set()
    # Repeatedly emit steps whose deps are all resolved (stable by plan order).
    progressed = True
    while progressed and len(ordered) < len(nums):
        progressed = False
        for n in nums:
            if n not in resolved and all(d in resolved for d in deps[n]):
                ordered.append(n)
                resolved.add(n)
                progressed = True
    # Any steps left are in a cycle — append in plan order (best effort).
    for n in nums:
        if n not in resolved:
            ordered.append(n)
    return ordered


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


def extract_budget(query: str) -> float | None:
    """Extract budget from query if present."""
    import re

    patterns = [
        r'\$([\d,]+)',
        r'budget[:\s]+[\$£€]?([\d,]+)',
        r'([\d,]+)\s*(?:dollars?|usd)',
    ]

    for pattern in patterns:
        match = re.search(pattern, query.lower())
        if match:
            try:
                return float(match.group(1).replace(',', ''))
            except ValueError:
                continue

    return None


def extract_constraints(query: str) -> list[str]:
    """Extract constraints from query."""
    constraints = []
    query_lower = query.lower()

    if "vegetarian" in query_lower:
        constraints.append("must have vegetarian options")
    if "15 people" in query_lower or "15 people" in query_lower:
        constraints.append("suitable for 15 people")
    if "conference" in query_lower:
        constraints.append("must have conference room")

    return constraints


def generate_task_id(query: str) -> str:
    """Generate a unique task ID from query."""
    return hashlib.md5(query.encode()).hexdigest()[:16]


async def handle_multi_step_task(
    query: str,
    agent: Agent,
    session: Session,
    llm: LLM,
    stream: bool = False,
    interface: str = 'cli',
) -> str:
    """Handle a multi-step task with planning and verification.

    `interface` is threaded into tool approval: CLI callers prompt on the
    console; web/ws callers must pass their own interface so risky tools are
    denied by policy instead of blocking the server on input().
    """

    tracker = get_task_tracker()
    task_id = generate_task_id(query)

    # Extract metadata from query
    budget = extract_budget(query)
    constraints = extract_constraints(query)

    # Validate budget - cap at reasonable maximum to prevent resource exhaustion
    MAX_BUDGET = 1_000_000  # $1M max
    if budget and budget > MAX_BUDGET:
        logger.warning(f"Budget {budget} exceeds max {MAX_BUDGET}, capping")
        budget = MAX_BUDGET
    elif budget and budget <= 0:
        logger.warning(f"Invalid budget {budget}, ignoring")
        budget = None

    console.print(Panel(
        "[bold cyan]Planning multi-step task...[/bold cyan]",
        title="[blue]Task Analysis[/blue]",
        border_style="blue"
    ))

    # Generate plan using the planner
    planner = StructuredPlanner(llm)

    # Get available agents (current agent is included)
    available_agents = [agent]
    available_tools = agent.tools

    # Build enhanced task description
    task_description = query
    if budget:
        task_description += f"\n\nBudget: ${budget}"
    if constraints:
        task_description += f"\n\nConstraints: {', '.join(constraints)}"

    try:
        plan = await planner.create_plan(
            task_description,
            available_agents,
            available_tools,
            budget=budget,
            constraints=constraints
        )

        # Convert plan to workflow format
        workflow_steps = []
        for step in plan.steps:
            workflow_steps.append({
                "step_number": step.step_number,
                "title": step.title,
                "description": step.description,
                "assigned_agent": step.assigned_agent,
                "dependencies": step.dependencies,
                "verification_criteria": step.verification_criteria,
                "expected_output": step.expected_output
            })

        # Initialize task tracking
        tracker.start_task(
            task_id=task_id,
            goal=query,
            plan=workflow_steps,
            budget=budget,
            constraints=constraints
        )

        console.print(Panel(
            f"[bold green]Plan created with {len(workflow_steps)} steps[/bold green]\n\n" +
            "\n".join(f"  {i+1}. {s['title']}" for i, s in enumerate(workflow_steps)),
            title="[green]Execution Plan[/green]",
            border_style="green"
        ))

        # Execute steps in DEPENDENCY order (topological batches), not raw plan
        # order — otherwise a step can run before the steps it depends on.
        try:
            batches = planner.get_execution_order(plan)
            ordered_numbers = [s.step_number for batch in batches for s in batch]
        except Exception as e:
            logger.warning(f"Could not compute execution order ({e}); falling back to a dependency sort")
            ordered_numbers = _topological_fallback(workflow_steps)
        steps_by_num = {s["step_number"]: s for s in workflow_steps}
        ordered_steps = [steps_by_num[n] for n in ordered_numbers if n in steps_by_num]

        # Execute steps sequentially (in dependency order) with verification
        results = []
        start_time = __import__('time').time()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:

            for step in ordered_steps:
                task_desc = f"Step {step['step_number']}: {step['title']}"
                progress_task_id = progress.add_task(task_desc, total=None)

                # Build context for this step
                step_context = tracker.build_step_context(task_id, step)

                # Execute step
                step_output = await execute_step(
                    step_context, agent, session, step, stream, interface
                )

                # Record result. Success = produced non-empty output that isn't an
                # error message (a length check alone counted error strings as success).
                out_stripped = (step_output or "").strip()
                step_success = bool(out_stripped) and not out_stripped.lower().startswith("error")
                result = StepResult(
                    step_number=step["step_number"],
                    title=step["title"],
                    output=step_output,
                    success=step_success,
                    verification_passed=False
                )
                tracker.add_step_result(task_id, result)
                results.append(result)

                # Verify step completion
                if not await verify_step(step, step_output):
                    console.print(Panel(
                        f"[bold yellow]Step {step['step_number']} may be incomplete[/bold yellow]\n"
                        f"Output: {step_output[:200]}...",
                        title="[yellow]Verification Warning[/yellow]",
                        border_style="yellow"
                    ))
                else:
                    tracker.mark_step_verified(task_id, step["step_number"])

                progress.update(progress_task_id, completed=True)

        # Calculate total duration
        total_duration = __import__('time').time() - start_time
        if total_duration >= 60:
            duration_str = f"{int(total_duration // 60)}m {int(total_duration % 60)}s"
        else:
            duration_str = f"{total_duration:.1f}s"

        # Check if task completed
        if tracker.is_task_complete(task_id):
            console.print(Panel(
                f"[bold green]All steps completed and verified![/bold green]\n\nTotal time: {duration_str}",
                title="[green]Task Complete[/green]",
                border_style="green"
            ))

        # Generate final synthesis
        final_output = synthesize_results(tracker, task_id, query)

        return final_output

    except Exception as e:
        console.print(Panel(
            f"[bold red]Planning failed: {str(e)}[/bold red]",
            title="[red]Error[/red]",
            border_style="red"
        ))
        raise


async def execute_step(
    context: str,
    agent: Agent,
    session: Session,
    step: dict,
    stream: bool,
    interface: str = 'task',
) -> str:
    """Execute a single step and return output."""

    response = ""

    # Must be async: for non-cli interfaces the session loop `await`s the output
    # callback. The payload is a dict ({'content': ...}); read its content rather
    # than str()-ing the whole dict.
    async def capture_response(*args, **kwargs):
        nonlocal response
        payload = args[0] if args else kwargs
        if isinstance(payload, dict):
            content = payload.get('content', '')
        else:
            content = str(payload) if payload else ''
        if content:
            response += content

    # The session's console-printing branches key off interface == 'cli' and
    # would call the async capture without awaiting it — so CLI callers run
    # steps as 'task' (approval still prompts on the console); web/ws callers
    # keep their own interface so risky tools are denied by policy.
    session_interface = 'task' if interface in ('cli', 'task') else interface
    await session(
        context,
        agent,
        session_interface,
        stream,
        capture_response,
        {}
    )

    # A tool-heavy step (or one that hit the tool-round cap) can stream no final
    # text — fall back to a summary of what it actually did so the synthesized
    # report and verification aren't blank.
    result = response.strip()
    if not result:
        result = _summarize_recent_activity(session)
    return result or "(step completed with no textual output)"


def _summarize_recent_activity(session, window: int = 24) -> str:
    """Best-effort summary of a step's turn from the session tail: the last
    assistant text, else a tally of the tools it invoked."""
    from collections import Counter

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


# Path-like tokens with a known code/doc extension (avoids matching version
# numbers like "1.0.0"). Used to verify file-producing steps by their actual
# side effects rather than an LLM's opinion of the narration.
_KNOWN_EXT = (
    'py|md|txt|json|toml|yaml|yml|cfg|ini|js|ts|tsx|jsx|html|css|csv|rst|sh|'
    'log|xml|sql|go|rs|java|c|cpp|h|env|lock'
)
_PATH_TOKEN_RE = re.compile(rf'[A-Za-z0-9_./\\-]+\.(?:{_KNOWN_EXT})\b')
_SKIP_DIRS = {
    '.git', '__pycache__', 'node_modules', '.venv', 'venv', '.mypy_cache',
    '.pytest_cache', '.ruff_cache', 'dist', 'build', '.tox',
}


def _referenced_paths(text: str) -> list[str]:
    """File paths a step's spec says it should produce."""
    return list(dict.fromkeys(_PATH_TOKEN_RE.findall(text or '')))


def _nonempty_file(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _file_present(candidate: str, root: Path) -> bool:
    """Does the referenced file now exist (non-empty)? Tries the path as given
    (relative to root or absolute), then a bounded search for its basename so a
    bare 'REPORT.md' still matches 'live_build/REPORT.md'."""
    p = Path(candidate)
    if _nonempty_file(p if p.is_absolute() else root / p):
        return True
    name = p.name
    if not name:
        return False
    checked = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if name in filenames and _nonempty_file(Path(dirpath) / name):
            return True
        checked += len(filenames)
        if checked > 4000:
            break
    return False


def _looks_like_error(text: str) -> bool:
    t = (text or '').strip().lower()
    return (not t) or t.startswith('error') or 'traceback (most recent call last)' in t


async def verify_step(step: dict, output: str, llm: LLM | None = None) -> bool:
    """Verify a step by its actual side effects, not a text-only LLM opinion.

    - If the step's spec references concrete file paths, it's verified when at
      least one of them now exists on disk (the previous LLM verifier could not
      see the filesystem, so it returned false 'incomplete' on successful file-
      producing steps).
    - Otherwise fall back to the output heuristic (non-empty, not an error)
      rather than a second, unreliable LLM call.
    """
    spec = ' '.join(str(step.get(k, '') or '') for k in
                    ('verification_criteria', 'expected_output', 'description'))
    referenced = _referenced_paths(spec)
    if referenced:
        from cognitrix.config import settings
        root = Path(getattr(settings, 'tools_root', None) or Path.cwd())
        return any(_file_present(c, root) for c in referenced)

    return not _looks_like_error(output)


def synthesize_results(tracker, task_id: str, original_query: str) -> str:
    """Synthesize all step results into final output."""

    task = tracker.tasks.get(task_id)
    if not task:
        return "Task not found"

    results = tracker.step_results.get(task_id, [])

    output_parts = [
        f"# Task: {task.original_goal}",
        ""
    ]

    if task.budget:
        output_parts.append(f"**Budget:** ${task.budget}")

    if task.constraints:
        output_parts.append(f"**Constraints:** {', '.join(task.constraints)}")

    output_parts.append("")
    output_parts.append("---")
    output_parts.append("")

    for result in results:
        status = "[DONE]" if result.verification_passed else "[PENDING]"
        output_parts.append(f"## {status} Step {result.step_number}: {result.title}")
        output_parts.append("")
        output_parts.append(result.output)
        output_parts.append("")

    return "\n".join(output_parts)


def get_task_progress(task_id: str) -> str | None:
    """Get progress summary for a task."""
    tracker = get_task_tracker()
    return tracker.get_summary(task_id)
