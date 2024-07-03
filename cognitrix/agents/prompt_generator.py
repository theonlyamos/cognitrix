from pydantic import Field
from ..agents.base import Agent
from ..prompts.meta import meta_template

class PromptGenerator(Agent):
    """_summary_
    """
    name: str = Field(default='Prompt Generator')
    prompt_template: str = Field(default=meta_template)