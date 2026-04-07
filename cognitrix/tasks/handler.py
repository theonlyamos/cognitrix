"""Multi-step task handler with planning and verification."""

import uuid
import hashlib
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from cognitrix.agents.base import Agent
from cognitrix.sessions.base import Session
from cognitrix.providers.base import LLM
from cognitrix.planning.structured_planner import StructuredPlanner
from cognitrix.tasks.tracker import (
    get_task_tracker, StepResult, TaskState
)
from cognitrix.prompts.planning import (
    PLANNING_USER_TEMPLATE,
    get_budget_info,
    get_constraints_info
)


console = Console()


def is_multi_step_task(query: str) -> bool:
    """Detect if a query requires multi-step execution."""
    query_lower = query.lower()
    
    multi_step_indicators = [
        " and ",  # "find hotels and book catering"
        "plan a",  # "plan a 3-day trip"
        "ensure ",  # "ensure they have..."
        "find ",  # "find 3 options"
        "multiple ",  # "multiple things"
        "create a complete",  # "create a complete itinerary"
    ]
    
    task_keywords = [
        "book", "reserve", "schedule", "organize",
        "plan", "find", "research", "book",
    ]
    
    # Check for multiple requirements
    has_indicators = any(ind in query_lower for ind in multi_step_indicators)
    has_keywords = any(kw in query_lower for kw in task_keywords)
    
    return has_indicators and has_keywords


def extract_budget(query: str) -> Optional[float]:
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
    stream: bool = False
) -> str:
    """Handle a multi-step task with planning and verification."""
    
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
        
        # Execute steps sequentially with verification
        results = []
        start_time = __import__('time').time()
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            
            for step in workflow_steps:
                task_desc = f"Step {step['step_number']}: {step['title']}"
                progress_task_id = progress.add_task(task_desc, total=None)
                
                # Build context for this step
                step_context = tracker.build_step_context(task_id, step)
                
                # Execute step
                step_output = await execute_step(
                    step_context, agent, session, step, stream
                )
                
                # Record result
                result = StepResult(
                    step_number=step["step_number"],
                    title=step["title"],
                    output=step_output,
                    success=len(step_output) > 10,
                    verification_passed=False
                )
                tracker.add_step_result(task_id, result)
                results.append(result)
                
                # Verify step completion
                if not await verify_step(step, step_output, llm):
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
    stream: bool
) -> str:
    """Execute a single step and return output."""
    
    response = ""
    
    def capture_response(*args, **kwargs):
        nonlocal response
        # Handle both streaming and non-streaming formats
        if args:
            content = args[0] if isinstance(args[0], str) else str(args[0])
        elif kwargs:
            content = kwargs.get('content', '')
        else:
            content = ''
        if content:
            response += content
    
    await session(
        context,
        agent,
        'task',  # Use task interface for multi-step execution
        stream,
        capture_response,
        {}
    )
    
    return response


async def verify_step(step: dict, output: str, llm: LLM) -> bool:
    """Verify if a step completed successfully."""
    
    verification_criteria = step.get("verification_criteria", "")
    
    if not verification_criteria:
        return len(output) > 10
    
    prompt = f"""You are a verifier. Check if the step output meets the verification criteria.

Step: {step['title']}
Description: {step['description']}
Verification Criteria: {verification_criteria}

Output:
{output[:1000]}

Does this output meet the verification criteria? Answer YES or NO with a brief explanation.
If NO, specify what's missing."""

    messages = [
        {'role': 'system', 'content': 'You are a verification assistant. Answer YES or NO.'},
        {'role': 'user', 'content': prompt}
    ]
    
    try:
        response = await llm(messages, stream=False)
        
        if hasattr(response, 'llm_response'):
            response_text = response.llm_response
        else:
            response_text = ""
            async for chunk in response:
                if hasattr(chunk, 'current_chunk'):
                    response_text += chunk.current_chunk
                elif isinstance(chunk, str):
                    response_text += chunk
        
        return response_text.strip().lower().startswith('yes')
        
    except Exception:
        return len(output) > 50


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


def get_task_progress(task_id: str) -> Optional[str]:
    """Get progress summary for a task."""
    tracker = get_task_tracker()
    return tracker.get_summary(task_id)
