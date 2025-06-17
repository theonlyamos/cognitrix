from pydantic import Field

from ..agents.base import Agent
from ..prompts.instructions import tasks_instructor
from ..prompts.meta import meta_template


class PromptGenerator(Agent):
    """_summary_
    """
    name: str = Field(default='Prompt Generator')
    system_prompt: str = Field(default=meta_template)

class TaskInstructor(Agent):
    """_summary_
    """
    name: str = Field(default='Tasks Instructor')
    system_prompt: str = Field(default=tasks_instructor)
