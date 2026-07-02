from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.sessions.base import Session

class BaseContextManager(ABC):
    """Abstract base class for context managers."""

    @abstractmethod
    async def build_prompt(self, agent: 'Agent', session: 'Session') -> list[dict[str, Any]]:
        """
        Builds a context-aware prompt for the LLM.

        Args:
            agent: The agent that will process the prompt.
            session: The current session containing the chat history.

        Returns:
            A list of dictionaries formatted for the LLM provider.
        """
        pass

def _trim_to_valid_start(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop leading orphan tool-result messages.

    A `role:'tool'` message is only valid immediately after the assistant
    message that issued the matching tool_calls. If a window slice (or a legacy
    history without persisted tool_calls) begins on a tool message, that message
    has no preceding assistant call, which providers reject — so drop the leading
    tool messages until the window starts on a normal message.
    """
    i = 0
    while i < len(messages) and str(messages[i].get('role', '')).lower() == 'tool':
        i += 1
    return messages[i:]


class SlidingWindowContextManager(BaseContextManager):
    """
    A context manager that uses a simple sliding window of the most recent messages.
    """
    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages

    async def build_prompt(self, agent: 'Agent', session: 'Session') -> list[dict[str, Any]]:
        """
        Builds a prompt using the system prompt and the last `max_messages` from the history.
        """
        system_prompt = {
            'role': 'system',
            'content': agent.formatted_system_prompt()
        }

        # Get the last `max_messages`, then trim so the window can't begin on an
        # orphan tool-result message (would violate the tool-call protocol).
        recent_history = _trim_to_valid_start(session.chat[-self.max_messages:])

        prompt = [system_prompt] + recent_history
        return prompt
