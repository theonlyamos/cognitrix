import re
import uuid
from typing import Any

from odbms import Model
from pydantic import Field


class Tool(Model):
    """
    Base tool class for data modeling.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for the tool"""

    name: str
    """Name of the tool"""

    description: str
    """Description of what the tool does and how to use it"""

    category: str = "general"
    """Category of the tool. Used for grouping"""

    parameters: Any = {}
    """Used for type hinting for function tools"""

    required_params: list[str] | None = None
    """Names of parameters without defaults. If None, all parameters are required."""

    user_id: str | None = Field(default=None)
    """User ID of the user the tool belongs to"""

    retryable: bool = True
    """Whether a failed invocation may be retried by the runtime."""

    max_attempts: int = 3
    """Maximum provider/runtime attempts for a single call."""

    max_calls_per_turn: int | None = None
    """Optional per-agent-turn cap, used for side-effecting tools."""

    supported_interfaces: list[str] | None = None
    """Runtime interfaces allowed to execute this capability, or all when None."""

    approval_mode: str = 'risk_based'
    """risk_based, assigned_only, or always."""

    class Config:
        arbitrary_types_allowed = True

    async def run(self, *args, **kwargs):
        """Here is where you code what the tool does"""
        pass

    async def arun(self, *args, **kwargs):
        """Asynchronous implementation"""
        pass

    def to_dict_format(self) -> dict[str, Any]:
        """
        Converts an instance of the Tool class to a dictionary in the OpenAI API format.

        Returns:
            Dict[str, Any]: A dictionary representing the tool in the OpenAI API format.
        """
        def python_type_to_json_type(py_type: str) -> str:
            """Convert Python types to JSON Schema types"""
            normalized = py_type.lower().replace('typing.', '')
            if normalized.startswith('list[') or normalized.startswith('sequence['):
                return 'array'
            if normalized.startswith('dict['):
                return 'object'
            type_mapping = {
                'str': 'string',
                'int': 'integer',
                'float': 'number',
                'bool': 'boolean',
                'list': 'array',
                'dict': 'object',
                'None': 'null'
            }
            return type_mapping.get(normalized, 'string')

        parameters = self.parameters if (hasattr(self, 'parameters') and self.parameters) else {}

        desc = self.description or ''

        # Function-level description: first paragraph only. The Args/Returns/
        # Examples docstring sections duplicate what the parameters schema
        # conveys and roughly double the token floor of every prompt.
        summary = re.split(
            r'\n\s*\n|\n\s*(?:Args|Arguments|Returns|Raises|Examples?)\s*:', desc, maxsplit=1
        )[0].strip() or desc[:200]

        # Per-parameter descriptions from ':param name: ...' lines or
        # Google-style '    name (type): ...' Args lines.
        param_descs: dict[str, str] = {}
        for m in re.finditer(r':param\s+(\w+)\s*:\s*(.+)', desc):
            param_descs[m.group(1)] = m.group(2).strip()
        for m in re.finditer(r'^\s+(\w+)\s*\([^)]*\)\s*:\s*(.+)$', desc, re.MULTILINE):
            param_descs.setdefault(m.group(1), m.group(2).strip())

        properties = {}
        for name, param_type in parameters.items():
            vtype = python_type_to_json_type(param_type)
            prop = {"type": vtype, "description": param_descs.get(name, "")}
            if vtype == 'array':
                item_type = 'string'
                if isinstance(param_type, str) and '[' in param_type:
                    item_type = python_type_to_json_type(param_type.split('[', 1)[1].rstrip(']'))
                prop['items'] = {"type": item_type} # type: ignore
            properties[name] = prop

        # Only params without defaults are required (if we know them); otherwise
        # fall back to marking all required.
        source = self.required_params if self.required_params is not None else list(parameters.keys())
        required = [p for p in source if p in properties]

        # Always return a valid function schema, even for zero-argument tools,
        # so parameterless tools are still advertised to the model.
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(' ', '_'),
                "description": summary[:1024],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }

class MCPTool(Tool):
    """A dynamic tool created from an MCP server definition."""
    mcp_schema: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any):
        super().__init__(**data)
        if 'run' in data:
            self.run = data['run']

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
