from pydantic import Field
from ..agents.base import Agent
from ..agents.templates import ASSISTANT_SYSTEM_PROMPT

class AIAssistant(Agent):
    """_summary_

    Args:
        Agent (_type_): _description_
    """
    system_prompt: str = Field(default=ASSISTANT_SYSTEM_PROMPT)