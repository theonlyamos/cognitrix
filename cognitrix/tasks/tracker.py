"""Task tracking for multi-step workflows."""

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum
import json


class TaskState(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepResult:
    step_number: int
    title: str
    output: str
    success: bool
    verification_passed: bool = False


@dataclass
class TaskContext:
    original_goal: str
    task_analysis: str = ""
    estimated_complexity: str = "moderate"
    budget: Optional[float] = None
    constraints: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "original_goal": self.original_goal,
            "task_analysis": self.task_analysis,
            "estimated_complexity": self.estimated_complexity,
            "budget": self.budget,
            "constraints": self.constraints,
        }


class TaskTracker:
    """Tracks progress of multi-step tasks and accumulates results."""
    
    def __init__(self):
        self.tasks: dict[str, TaskContext] = {}
        self.step_results: dict[str, list[StepResult]] = {}
        self.current_step: dict[str, int] = {}
        self.plan: dict[str, list[dict]] = {}
    
    def start_task(
        self, 
        task_id: str, 
        goal: str, 
        plan: list[dict],
        budget: Optional[float] = None,
        constraints: list[str] = None
    ):
        """Initialize tracking for a new task."""
        self.tasks[task_id] = TaskContext(
            original_goal=goal,
            budget=budget,
            constraints=constraints or []
        )
        self.step_results[task_id] = []
        self.current_step[task_id] = 0
        self.plan[task_id] = plan
    
    def get_current_step(self, task_id: str) -> Optional[dict]:
        """Get the current step to execute."""
        if task_id not in self.plan:
            return None
        
        steps = self.plan[task_id]
        idx = self.current_step.get(task_id, 0)
        
        if idx >= len(steps):
            return None
        
        return steps[idx]
    
    def add_step_result(self, task_id: str, result: StepResult):
        """Record result from a step execution."""
        if task_id not in self.step_results:
            self.step_results[task_id] = []
        self.step_results[task_id].append(result)
        self.current_step[task_id] = result.step_number + 1
    
    def mark_step_verified(self, task_id: str, step_number: int):
        """Mark a step as verified (passed self-check)."""
        if task_id in self.step_results:
            for result in self.step_results[task_id]:
                if result.step_number == step_number:
                    result.verification_passed = True
    
    def is_task_complete(self, task_id: str) -> bool:
        """Check if all steps are completed and verified."""
        if task_id not in self.plan:
            return False
        
        steps = self.plan[task_id]
        results = self.step_results.get(task_id, [])
        
        # All steps must have results
        if len(results) != len(steps):
            return False
        
        # All steps must be verified
        return all(r.verification_passed for r in results)
    
    def get_pending_steps(self, task_id: str) -> list[dict]:
        """Get steps that haven't been executed yet."""
        if task_id not in self.plan:
            return []
        
        completed = {r.step_number for r in self.step_results.get(task_id, [])}
        return [s for s in self.plan[task_id] if s["step_number"] not in completed]
    
    def get_accumulated_context(self, task_id: str) -> str:
        """Build context string from all completed step results."""
        if task_id not in self.step_results:
            return ""
        
        context_parts = ["## Accumulated Findings\n"]
        
        for result in self.step_results[task_id]:
            context_parts.append(f"### Step {result.step_number}: {result.title}")
            context_parts.append(result.output)
            context_parts.append("")
        
        return "\n".join(context_parts)
    
    def build_step_context(self, task_id: str, step: dict) -> str:
        """Build context for executing a specific step."""
        parts = [
            f"Task: {self.tasks[task_id].original_goal}",
            f"Step {step['step_number']}: {step['title']}",
            f"Description: {step['description']}",
            ""
        ]
        
        # Add dependencies results
        deps = step.get("dependencies", [])
        if deps:
            parts.append("Previous step results:")
            for dep_num in deps:
                for result in self.step_results.get(task_id, []):
                    if result.step_number == dep_num:
                        parts.append(f"Step {dep_num}: {result.output[:300]}...")
            parts.append("")
        
        # Add all accumulated context for reference
        if self.step_results.get(task_id):
            parts.append("## All completed steps:")
            parts.append(self.get_accumulated_context(task_id))
            parts.append("")
        
        parts.append("Execute this step and provide your output.")
        
        return "\n".join(parts)
    
    def get_verification_prompt(self, task_id: str, step: dict) -> str:
        """Generate self-reflection prompt for step verification."""
        verification = step.get("verification_criteria", "")
        
        return f"""Verify if the step completed successfully:

Step: {step['title']}
Description: {step['description']}
Verification criteria: {verification}

Did this step meet the verification criteria? Answer YES or NO and explain why.
If NO, specify what is missing and what needs to be done next."""
    
    def get_summary(self, task_id: str) -> str:
        """Generate summary of task progress."""
        if task_id not in self.tasks:
            return "Task not found"
        
        task = self.tasks[task_id]
        results = self.step_results.get(task_id, [])
        
        lines = [
            f"Goal: {task.original_goal}",
            f"Progress: {len(results)}/{len(self.plan.get(task_id, []))} steps completed",
            f"Budget: {task.budget}" if task.budget else "Budget: Not specified",
            ""
        ]
        
        for result in results:
            status = "✓" if result.verification_passed else "✗"
            lines.append(f"  {status} Step {result.step_number}: {result.title}")
        
        return "\n".join(lines)
    
    def cancel_task(self, task_id: str):
        """Cancel tracking for a task."""
        if task_id in self.tasks:
            del self.tasks[task_id]
        if task_id in self.step_results:
            del self.step_results[task_id]
        if task_id in self.current_step:
            del self.current_step[task_id]
        if task_id in self.plan:
            del self.plan[task_id]


# Global task tracker instance
_task_tracker = TaskTracker()


def get_task_tracker() -> TaskTracker:
    """Get the global task tracker instance."""
    return _task_tracker
