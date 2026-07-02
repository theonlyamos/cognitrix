"""Planning prompts and Pydantic models for structured plan generation."""

from pydantic import BaseModel, Field


class Step(BaseModel):
    """A single step in a workflow plan."""
    step_number: int = Field(..., description="Sequential step number (1-based)")
    title: str = Field(..., description="Short, descriptive title")
    description: str = Field(..., description="Detailed description of what to do")
    expected_output: str = Field(..., description="What this step should produce")
    assigned_agent: str = Field(default="auto", description="Agent name or 'auto' for automatic assignment")
    required_tools: list[str] = Field(default_factory=list, description="Tools needed for this step")
    dependencies: list[int] = Field(default_factory=list, description="Step numbers this step depends on")
    estimated_duration: str = Field(default="medium", description="short/medium/long")
    verification_criteria: str = Field(..., description="How to verify this step succeeded")


class TaskPlan(BaseModel):
    """Complete plan for executing a task."""
    task_analysis: str = Field(..., description="Brief analysis of what needs to be done")
    estimated_complexity: str = Field(..., description="simple/moderate/complex")
    steps: list[Step] = Field(..., description="Ordered list of steps to execute")
    parallel_groups: list[list[int]] = Field(
        default_factory=list,
        description="Groups of step numbers that can run in parallel"
    )
    fallback_strategy: str = Field(..., description="What to do if the main approach fails")


PLANNING_SYSTEM_PROMPT = """You are an expert task planner. Break down complex tasks into concrete, actionable steps.

## CRITICAL OUTPUT REQUIREMENTS - YOU MUST FOLLOW THESE EXACTLY
- Return ONLY valid JSON - start with { and end with }
- Do NOT use markdown code blocks (no ```json or ```)
- Do NOT include any text before or after the JSON
- All fields in the TaskPlan schema are REQUIRED
- The JSON must be parseable by json.loads()

## Schema
{
  "task_analysis": "string - brief analysis of what needs to be done",
  "estimated_complexity": "simple|moderate|complex",
  "steps": [
    {
      "step_number": 1,
      "title": "string",
      "description": "string",
      "expected_output": "string",
      "assigned_agent": "string",
      "required_tools": [],
      "dependencies": [],
      "estimated_duration": "short|medium|long",
      "verification_criteria": "string"
    }
  ],
  "parallel_groups": [],
  "fallback_strategy": "string"
}

## Rules
- Create 3-10 steps depending on task complexity
- Each step must have clear verification criteria
- Mark dependencies explicitly (steps that must complete before this one)
- Identify steps that can run in parallel
- Consider tool requirements for each step

## Assigning agents
- Set "assigned_agent" to one of the names listed under "## Available Agents"
  in the task message, choosing the best fit for the step.
- If no listed agent fits (or you are unsure), use "auto" and the system will
  route the step automatically. Never invent agent names.

## Critical Requirements
- ALWAYS verify completion before moving to next step
- Track specific requirements (e.g., "3 hotels", "vegetarian options")
- Consider budget constraints in planning
- Include fallback strategies for failed searches

## Output Format
Return ONLY valid JSON matching the TaskPlan schema. Do not include markdown formatting or explanations outside the JSON."""


PLANNING_USER_TEMPLATE = """Create a detailed plan for the following task:

## Task
{task}

## Available Agents
{agents}

## Available Tools
{tools}

## Constraints (MUST respect)
{budget_info}
{constraints}

Generate a structured plan with steps, dependencies, and parallelization opportunities.
Each step MUST include specific verification criteria that can be checked."""


def get_budget_info(budget: float = None) -> str:
    """Format budget info for planning prompt."""
    if budget:
        return f"- Total budget: ${budget:.2f}\n- Must stay within budget"
    return "- No budget constraints specified"


def get_constraints_info(constraints: list[str]) -> str:
    """Format constraints for planning prompt."""
    if not constraints:
        return "- No specific constraints"
    return "- Required: " + "\n- Required: ".join(constraints)
