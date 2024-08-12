from pydantic import Field
from ..agents.base import Agent
from ..prompts.evaluation import evaluation_prompt

class Evaluator(Agent):
    name: str = Field(default='Evaluator')
    system_prompt: str = Field(default=evaluation_prompt)
    