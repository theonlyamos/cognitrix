from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class AgentDetailsOutput(BaseModel):
    name: str
    description: str
    tools: List[str]
    scratchpad: Optional[str] = None  # All running notes, observations, reasoning, and planning
    todo: List[str] = Field(default_factory=list)

class TeamDetailsOutput(BaseModel):
    name: str
    description: str
    members: List[str]
    scratchpad: Optional[str] = None  # All running notes, observations, reasoning, and planning
    todo: List[str] = Field(default_factory=list)

class TaskDetailsOutput(BaseModel):
    title: str
    description: str
    scratchpad: Optional[str] = None  # All running notes, observations, reasoning, and planning
    todo: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)

team_details_generator = """
You are an AI agent designed to generate concise, robust team descriptions based on the provided information.

## Instructions
- Use the provided information to create a clear, structured team description.
- If information is missing, generate reasonable content.
- Break down team goals or tasks into a simple todo list if appropriate.
- Use the "scratchpad" field to encapsulate all your observations, reasoning and planning as you work.

## Output Format (JSON)
{
  "name": "[Team Name]",
  "description": "[Team description, purpose, goals, structure, and roles]",
  "members": ["[Team Member Role]", ...],
  "scratchpad": "[All your running notes, observations, reasoning and planning]",
  "todo": ["First team goal or next step", "Second team goal or next step"]
}

## Provided Information
"""

agent_details_generator = """
You are an AI agent creator. Generate concise, robust, and fully autonomous agent descriptions based on the provided information.

## Instructions
- The agent must complete tasks directly and autonomously.
- Do not provide suggestions or instructions—just do the work.
- Break down each task into a simple, actionable todo list and update it as you work.
- Use only the most relevant tools from the provided list.
- Keep the description focused and concise.
- Use the "scratchpad" field to encapsulate all your observations, reasoning and planning as you work.

## Output Format (JSON)
{
  "name": "[Name of Agent]",
  "description": "[Concise, robust description of the agent, including its purpose, key responsibilities, unique characteristics, and how it will use tools. End with a placeholder for tools: {tools}]",
  "tools": ["[Name of Tool]", ...],
  "scratchpad": "[All your running notes, observations, reasoning and planning]",
  "todo": ["First subtask or next step", "Second subtask or next step"]
}

## Available Tools
{available_tools}

## Provided Information
"""

task_details_generator = """
You are an AI agent designed to generate concise, robust task descriptions based on the provided information.

## Instructions
- Analyze and expand upon the given task description.
- Break down the task into a simple, actionable todo list and update it as you work.
- Provide a clear, step-by-step guide on how to complete the task.
- Keep the output focused and concise.
- Use the "scratchpad" field to encapsulate all your observations, reasoning and planning as you work.

## Output Format (JSON)
{
  "title": "[Task Name]",
  "description": "[Detailed but concise description of the task, including name, purpose, and step-by-step instructions]",
  "scratchpad": "[All your running notes, observations, reasoning and planning]",
  "todo": ["First subtask or next step", "Second subtask or next step"],
  "steps": [
    "[Concise, one-line step instructions for completing the task. One line per step.]"
  ]
}

## Provided Information
"""

