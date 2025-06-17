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

    user_id: str | None = Field(default=None)
    """User ID of the user the tool belongs to"""

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
            type_mapping = {
                'str': 'string',
                'int': 'integer',
                'float': 'number',
                'bool': 'boolean',
                'list': 'array',
                'dict': 'object',
                'None': 'null'
            }
            return type_mapping.get(py_type, 'string')

        tool_json = {}
        if hasattr(self, 'parameters') and self.parameters:
            parameters = self.parameters
            required = list(parameters.keys())

            properties = {}
            for name, param_type in parameters.items():
                vtype = python_type_to_json_type(param_type)
                prop = {"type": vtype, "description": ""}
                if vtype == 'array':
                    prop['items'] = {"type": "string"} # type: ignore
                properties[name] = prop

            tool_json = {
                "type": "function",
                "function": {
                    "name": self.name.replace(' ', '_'),
                    "description": self.description[:1024],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }
            }

        return tool_json

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
