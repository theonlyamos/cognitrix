
from pydantic import BaseModel, Field


class TaskInstructionOutput(BaseModel):
    task_title: str
    task_description: str
    scratchpad: str | None = None  # All running notes, observations, reasoning, and planning
    todo: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)

tasks_instructor="""
You are an AI agent designed to analyze tasks and provide concise, robust breakdowns and instructions.

## Instructions
- Analyze and expand upon the given task description.
- Break down the task into a simple, actionable todo list and update it as you work.
- Provide a clear, step-by-step guide on how to complete the task.
- Keep your output focused and concise.
- Use the "scratchpad" field to encapsulate all your observations, reasoning and planning as you work.

## Output Format (JSON)
{
  "task_title": "[Title of the task]",
  "task_description": "[Brief, clear description of the task and its importance]",
  "scratchpad": "[All your running notes, observations, reasoning and planning]",
  "todo": ["First subtask or next step", "Second subtask or next step"],
  "steps": [
    "[Concise, one-line step instructions for completing the task. One line per step.]"
  ]
}

## Provided Information
"""

SELF_REFLECTION_PROMPT = """After completing the previous tool execution, perform self-reflection:

## Task Goal
{goal}

## What You Were Asked to Do
{task}

## Tool Output
{output}

## Self-Reflection Questions
1. Did I complete this specific sub-task? YES / NO
2. If NO, what is missing?
3. If YES, did I find what was requested (e.g., 3 hotels, vegetarian options)?
4. What should I do next to complete the overall goal?

Provide your reflection in this format:
- COMPLETE / INCOMPLETE
- Missing: [what's missing if incomplete]
- Next action: [what to do next]
"""

POST_TOOL_REFLECTION_PROMPT = """You just executed a tool. Reflect on the results:

Tool used: {tool_name}
Tool input: {tool_input}
Tool output: {output}

Original task: {task}

Answer these questions:
1. Did this tool call produce useful results? YES / NO
2. Did it answer the specific question asked? YES / NO  
3. What information is still needed?
4. Should I call this tool again with different parameters, or try a different tool?

Response format:
- Useful: YES/NO
- Answered question: YES/NO
- Still needed: [what's missing]
- Next step: [call same tool / call different tool / move on]"""
