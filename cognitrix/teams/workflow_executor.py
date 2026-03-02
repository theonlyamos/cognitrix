"""Workflow execution with dependency management and parallel execution."""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional
from enum import Enum

from cognitrix.agents.base import Agent
from cognitrix.sessions.base import Session
from cognitrix.teams.base import Team


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepResult:
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    execution_time: float = 0.0


@dataclass
class WorkflowStep:
    step_number: int
    title: str
    description: str
    assigned_agent: str
    dependencies: list[int]
    status: StepStatus = StepStatus.PENDING
    result: Optional[StepResult] = None


class WorkflowExecutor:
    """Executes workflow steps with parallelization and error handling."""
    
    def __init__(self, max_parallel: int = 3):
        self.max_parallel = max_parallel
        self.semaphore = asyncio.Semaphore(max_parallel)
    
    async def execute(
        self, 
        team: Team, 
        workflow: list[dict], 
        session: Session
    ) -> str:
        """
        Execute workflow with dependency-aware parallelization.
        
        Args:
            team: Team with agents
            workflow: List of step dictionaries
            session: Session for context
            
        Returns:
            Combined result of all steps
        """
        # Convert to WorkflowStep objects
        steps = [WorkflowStep(**step) for step in workflow]
        
        # Build dependency graph
        dependency_graph = self._build_dependency_graph(steps)
        
        # Track completed steps
        completed = set()
        results = []
        
        while len(completed) < len(steps):
            # Find ready steps (all dependencies satisfied)
            ready_steps = [
                s for s in steps 
                if s.step_number not in completed
                and all(d in completed for d in s.dependencies)
                and s.status == StepStatus.PENDING
            ]
            
            if not ready_steps:
                # Check for circular dependency or stuck workflow
                pending = [s for s in steps if s.step_number not in completed]
                if pending:
                    raise WorkflowError(f"Workflow stuck: {len(pending)} steps pending but none ready")
                break
            
            # Execute ready steps in parallel
            tasks = [
                self._execute_step_with_semaphore(step, team, session)
                for step in ready_steps
            ]
            step_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for step, result in zip(ready_steps, step_results):
                if isinstance(result, Exception):
                    step.status = StepStatus.FAILED
                    step.result = StepResult(success=False, error=str(result))
                    # Try recovery or fail workflow
                    if not await self._handle_step_failure(step, result, team, session):
                        raise WorkflowError(f"Step {step.step_number} failed: {result}")
                else:
                    step.status = StepStatus.COMPLETED
                    step.result = result
                    completed.add(step.step_number)
                    results.append(result.output)
        
        # Synthesize final result
        return await self._synthesize_results(steps, team, session)
    
    async def _execute_step_with_semaphore(
        self, 
        step: WorkflowStep, 
        team: Team, 
        session: Session
    ) -> StepResult:
        """Execute step with concurrency control."""
        async with self.semaphore:
            return await self._execute_step(step, team, session)
    
    async def _execute_step(
        self, 
        step: WorkflowStep, 
        team: Team, 
        session: Session
    ) -> StepResult:
        """Execute a single workflow step."""
        import time
        start_time = time.time()
        
        step.status = StepStatus.IN_PROGRESS
        
        # Find assigned agent
        agent = await self._get_agent_for_step(step, team)
        if not agent:
            return StepResult(
                success=False, 
                error=f"Agent '{step.assigned_agent}' not found"
            )
        
        try:
            # Build step prompt with context from dependencies
            prompt = await self._build_step_prompt(step, team)
            
            # Execute through session
            response = ""
            async def capture_response(data: dict):
                nonlocal response
                response += data.get('content', '')
            
            await session(prompt, agent, 'task', True, capture_response, 
                         {'type': 'workflow_step', 'action': f'step_{step.step_number}'})
            
            # Verify result
            if await self._verify_step_result(step, response, agent):
                return StepResult(
                    success=True,
                    output=response,
                    execution_time=time.time() - start_time
                )
            else:
                return StepResult(
                    success=False,
                    output=response,
                    error="Step verification failed",
                    execution_time=time.time() - start_time
                )
                
        except Exception as e:
            return StepResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
    
    async def _get_agent_for_step(self, step: WorkflowStep, team: Team) -> Optional[Agent]:
        """Find agent by name in team."""
        agents = await team.agents
        return next(
            (a for a in agents if a.name.lower() == step.assigned_agent.lower()),
            None
        )
    
    async def _build_step_prompt(self, step: WorkflowStep, team: Team) -> str:
        """Build prompt with context from completed dependencies."""
        prompt_parts = [
            f"Step #{step.step_number}: {step.title}",
            f"Description: {step.description}",
            ""
        ]
        
        # Add results from dependency steps
        if step.dependencies:
            prompt_parts.append("Context from previous steps:")
            for dep_num in step.dependencies:
                dep_step = next((s for s in team.tasks if s.step_number == dep_num), None)
                if dep_step and dep_step.result:
                    prompt_parts.append(f"Step {dep_num} result: {dep_step.result.output[:500]}")
            prompt_parts.append("")
        
        prompt_parts.append("Execute this step and provide your output.")
        
        return "\n".join(prompt_parts)
    
    async def _verify_step_result(
        self, 
        step: WorkflowStep, 
        result: str, 
        agent: Agent
    ) -> bool:
        """Verify step output meets expectations."""
        # Basic verification - non-empty and reasonable length
        if not result or len(result) < 10:
            return False
        
        # TODO: Add LLM-based verification for complex steps
        return True
    
    async def _handle_step_failure(
        self, 
        step: WorkflowStep, 
        error: Exception, 
        team: Team, 
        session: Session
    ) -> bool:
        """Attempt to recover from step failure."""
        # Log failure
        print(f"Step {step.step_number} failed: {error}")
        
        # TODO: Implement retry with different agent or strategy
        return False
    
    async def _synthesize_results(
        self, 
        steps: list[WorkflowStep], 
        team: Team, 
        session: Session
    ) -> str:
        """Combine all step results into final output."""
        results = []
        for step in steps:
            if step.result and step.result.success:
                results.append(f"## {step.title}\n{step.result.output}")
        
        return "\n\n".join(results)
    
    def _build_dependency_graph(self, steps: list[WorkflowStep]) -> dict[int, list[int]]:
        """Build dependency adjacency list."""
        return {s.step_number: s.dependencies for s in steps}


class WorkflowError(Exception):
    """Workflow execution error."""
    pass
