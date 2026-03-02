import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, Self

from fastapi import WebSocket
from odbms import Model
from pydantic import Field

from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM
from cognitrix.sessions.context import BaseContextManager

logger = logging.getLogger('cognitrix.log')


def _get_default_context_manager():
    """Factory function to create default context manager."""
    from cognitrix.memory.hybrid_context import HybridContextManager
    return HybridContextManager(agent_id="default")


class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class Message(Model):
    sender: str
    receiver: str
    content: str
    priority: MessagePriority = MessagePriority.NORMAL
    read: bool = False

class MCPTool(Tool):
    """A dynamic tool created from an MCP server definition."""
    mcp_schema: dict[str, Any] = Field(default_factory=dict)

    def to_dict_format(self) -> dict[str, Any]:
        """Returns the tool's schema directly from the MCP server's definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(' ', '_'),
                "description": self.description,
                "parameters": self.mcp_schema,
            }
        }

class Agent(Model):
    name: str = Field(default='Agent')
    """Name of the agent"""

    llm: LLM
    """LLM Provider to use for the agent"""

    tools: list[Tool] = Field(default=[])
    """List of tools to be use by the agent"""

    context_manager: 'BaseContextManager' = Field(default_factory=_get_default_context_manager)
    """The context manager for the agent."""

    def __init__(self, **data):
        super().__init__(**data)
        # Initialize context manager with agent ID if not provided
        if isinstance(self.context_manager, str) or self.context_manager is None:
            from cognitrix.memory.hybrid_context import HybridContextManager
            self.context_manager = HybridContextManager(agent_id=self.id)

    system_prompt: str
    """Agent's prompt template"""

    verbose: bool = Field(default=False)
    """Set agent verbosity"""

    sub_agents: list[Self] = Field(default=[])
    """List of sub agents which can be called by this agent"""

    mcp_servers: list[str] = Field(default_factory=list)
    """List of permitted MCP servers for this agent"""

    is_sub_agent: bool = Field(default=False)
    """Whether this agent is a sub agent for another agent"""

    parent_id: str | None = None
    """Id of this agent's parent agent (if it's a sub agent)"""

    autostart: bool = False
    """Whether the agent should start running as soon as it's created"""

    websocket: WebSocket | None = None
    """Websocket connection for web ui"""

    inbox: list[Message] = Field(default=[])
    """List of messages for the agent"""

    notification_callbacks: list[Callable[[Message], None]] = Field(default=[])
    """List of callbacks to be called when a message is added to the inbox"""

    response_list: list[tuple] = Field(default_factory=list)
    """List for storing responses from the agent"""

    class Config:
        arbitrary_types_allowed = True
