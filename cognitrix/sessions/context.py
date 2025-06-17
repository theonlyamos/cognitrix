from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.sessions.base import Session

class BaseContextManager(ABC):
    """Abstract base class for context managers."""

    @abstractmethod
    def build_prompt(self, agent: 'Agent', session: 'Session') -> list[dict[str, Any]]:
        """
        Builds a context-aware prompt for the LLM.

        Args:
            agent: The agent that will process the prompt.
            session: The current session containing the chat history.

        Returns:
            A list of dictionaries formatted for the LLM provider.
        """
        pass

class SlidingWindowContextManager(BaseContextManager):
    """
    A context manager that uses a simple sliding window of the most recent messages.
    """
    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages

    def build_prompt(self, agent: 'Agent', session: 'Session') -> list[dict[str, Any]]:
        """
        Builds a prompt using the system prompt and the last `max_messages` from the history.
        """
        system_prompt = {
            'role': 'system',
            'content': agent.formatted_system_prompt()
        }

        # Get the last `max_messages` from the session's chat history
        recent_history = session.chat[-self.max_messages:]

        prompt = [system_prompt] + recent_history
        return prompt
