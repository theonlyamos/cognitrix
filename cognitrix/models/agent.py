import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

from odbms import Model
from pydantic import Field, field_validator

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

    def get_context_manager(self) -> 'BaseContextManager':
        """Get context manager, creating lazily to avoid slow startup."""
        # Store in __dict__ to avoid odbms __getattr__ interception
        if '_ctx_mgr' not in self.__dict__:
            from cognitrix.memory.hybrid_context import HybridContextManager
            # Don't initialize yet - just store config
            self.__dict__['_ctx_mgr'] = None
            self.__dict__['_ctx_mgr_config'] = {
                'agent_id': str(self.id) if self.id else "default"
            }
        # Lazy initialize on first use
        if self.__dict__['_ctx_mgr'] is None:
            config = self.__dict__['_ctx_mgr_config']
            from cognitrix.memory.hybrid_context import HybridContextManager
            self.__dict__['_ctx_mgr'] = HybridContextManager(**config)
        return self.__dict__['_ctx_mgr']

    # Backward compatibility
    def get_memory(self) -> 'BaseContextManager':
        """Alias for get_context_manager."""
        return self.get_context_manager()

    system_prompt: str
    """Agent's prompt template"""

    verbose: bool = Field(default=False)
    """Set agent verbosity"""

    sub_agents: list["Agent"] = Field(default=[])
    """List of sub agents which can be called by this agent"""

    mcp_servers: list[str] = Field(default_factory=list)
    """List of permitted MCP servers for this agent"""

    folders: list[dict[str, Any]] | None = None
    """Default working folders ({path, access, name, primary}) copied into each
    new chat. None (never set) means the app's working folder; an empty list
    means each chat gets a private scratch folder."""

    can_request_folders: bool = True
    """Whether the agent may ask (with approval) for more folders or write access."""

    permission_level: str = 'auto'
    """How the agent asks before acting: auto (only risky actions ask), ask
    (every action asks) or allow (nothing asks). See cognitrix/safety/permissions.py."""

    tool_permissions: dict[str, str] = Field(default_factory=dict)
    """Per-tool levels that override permission_level: allow, ask or deny."""

    @field_validator('can_request_folders', mode='before')
    @classmethod
    def _can_request_folders_default(cls, value):
        # Agents saved before this column existed load it as NULL.
        return True if value is None else value

    @field_validator('permission_level', mode='before')
    @classmethod
    def _permission_level_default(cls, value):
        # Agents saved before these columns existed load them as NULL.
        return 'auto' if value in (None, '') else value

    @field_validator('tool_permissions', mode='before')
    @classmethod
    def _tool_permissions_default(cls, value):
        if value in (None, ''):
            return {}
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value)
            except ValueError:
                return {}
        return value if isinstance(value, dict) else {}

    is_sub_agent: bool = Field(default=False)
    """Whether this agent is a sub agent for another agent"""

    parent_id: str | None = None
    """Id of this agent's parent agent (if it's a sub agent)"""

    owner_id: str | None = None
    """User who owns this agent. None means a shared agent that only admins can change."""

    autostart: bool = False
    """Whether the agent should start running as soon as it's created"""

    websocket: Any = None
    """Websocket connection for web ui (typed Any to avoid importing fastapi at
    module scope just for this annotation — it added ~1s to every startup and is
    only assigned/duck-typed by the web path)."""

    inbox: list[Message] = Field(default=[])
    """List of messages for the agent"""

    notification_callbacks: list[Callable[[Message], None]] = Field(default=[])
    """List of callbacks to be called when a message is added to the inbox"""

    response_list: list[tuple] = Field(default_factory=list)
    """List for storing responses from the agent"""

    class Config:
        arbitrary_types_allowed = True
