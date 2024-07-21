from pydantic import Field
from ..agents.base import Agent
from ..prompts.meta import meta_template
from ..prompts.instructions import tasks_instructor

class PromptGenerator(Agent):
    """_summary_
    """
    name: str = Field(default='Prompt Generator')
    prompt_template: str = Field(default=meta_template)
    
class TaskInstructor(Agent):
    """_summary_
    """
    name: str = Field(default='Tasks Instructor')
    prompt_template: str = Field(default=tasks_instructor)