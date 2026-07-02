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
    """Trim the window so it never starts mid tool-exchange.

    A `role:'tool'` message is only valid immediately after the assistant
    message that issued the matching tool_calls, and stricter providers
    (Gemini) also reject an assistant tool_calls turn that doesn't directly
    follow a user/tool turn. Start the window at the first user message;
    if there is none, fall back to dropping leading tool/tool_calls messages.
    """
    for i, m in enumerate(messages):
        if str(m.get('role', '')).lower() == 'user':
            return messages[i:]
    i = 0
    while i < len(messages) and (
        str(messages[i].get('role', '')).lower() == 'tool' or messages[i].get('tool_calls')
    ):
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

        # A long tool loop can push the current turn's user message out of the
        # window, leaving nothing valid (an empty prompt is a provider error).
        # Anchor the window at the last user message instead — the whole
        # current turn stays in context (bounded by MAX_TOOL_ROUNDS).
        if not any(str(m.get('role', '')).lower() == 'user' for m in recent_history):
            for j in range(len(session.chat) - 1, -1, -1):
                if str(session.chat[j].get('role', '')).lower() == 'user':
                    recent_history = session.chat[j:]
                    break

        prompt = [system_prompt] + recent_history
        return prompt
