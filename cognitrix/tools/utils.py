import contextvars
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from odbms import Model
from PIL import Image
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ToolExecutionContext:
    """Immutable caller authority bound to one agent turn."""

    user_id: str | None = None
    api_key_id: str | None = None
    scopes: frozenset[str] | None = None
    allowed_agents: frozenset[str] | None = None
    allowed_teams: frozenset[str] | None = None

    @property
    def restricted(self) -> bool:
        return self.api_key_id is not None

    def has_scope(self, scope: str) -> bool:
        return self.scopes is None or scope in self.scopes

    def agent_allowed(self, agent_id: str) -> bool:
        return self.allowed_agents is None or agent_id in self.allowed_agents

    def team_allowed(self, team_id: str) -> bool:
        return self.allowed_teams is None or team_id in self.allowed_teams


_execution_context: contextvars.ContextVar[ToolExecutionContext] = contextvars.ContextVar(
    'tool_execution_context', default=ToolExecutionContext()
)


def current_execution_context() -> ToolExecutionContext:
    return _execution_context.get()


def set_execution_context(value: ToolExecutionContext):
    return _execution_context.set(value)


def reset_execution_context(token) -> None:
    _execution_context.reset(token)


class ToolResultType(Enum):
    """Enum for different types of tool results"""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    ERROR = "error"
    SUCCESS = "success"


class ArtifactRef(BaseModel):
    """A safe, client-facing reference to a stored tool artifact."""

    id: str
    mime_type: str
    filename: str | None = None
    width: int | None = None
    height: int | None = None
    origin: Literal['uploaded', 'generated'] | None = None


class EntityRef(BaseModel):
    """A concise reference to a domain object created or changed by a tool."""

    type: str
    id: str
    name: str


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolOutcome(BaseModel):
    """Stable tool result contract for models, sessions, and UI clients."""

    status: str
    text: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None

    @classmethod
    def success(cls, text: str, **kwargs: Any) -> 'ToolOutcome':
        return cls(status="success", text=text, **kwargs)

    @classmethod
    def failure(
        cls, code: str, message: str, *, denied: bool = False, retryable: bool = False
    ) -> 'ToolOutcome':
        return cls(
            status="denied" if denied else "error",
            text=message,
            error=ToolError(code=code, message=message, retryable=retryable),
        )

class ToolCallResult(Model):
    """Class to standardize tool execution results"""

    tool_name: str
    content: Any
    type: ToolResultType = ToolResultType.TEXT
    message: str | None = None
    metadata: dict[str, Any] | None = None
    outcome: ToolOutcome | None = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.determine_content_type()

    def determine_content_type(self):
        """Automatically detect type and create appropriate result"""

        if isinstance(self.content, Image.Image):
            self.type = ToolResultType.IMAGE
        elif isinstance(self.content, Path):
            self.type = ToolResultType.FILE
        elif isinstance(self.content, list | dict):
            self.content = json.dumps(self.content)

    def __str__(self) -> str:
        """String representation of the result"""
        return self.outcome.text if self.outcome else str(self.content)


def save_tool_as_json(name: str, description: str, category: str, function_code: str):
    """Save the tool information as a JSON file."""
    tool_info = {
        "name": name,
        "description": description,
        "category": category,
        "function_code": function_code
    }

    tools_dir = Path("custom_tools")
    tools_dir.mkdir(exist_ok=True)

    file_path = tools_dir / f"{name.lower().replace(' ', '_')}.json"
    with file_path.open('w') as f:
        json.dump(tool_info, f, indent=2)

def save_tool_as_python_file(name: str, description: str, category: str, function_code: str):
    """Save the tool as a Python file."""
    tools_dir = Path("custom_tools")
    tools_dir.mkdir(exist_ok=True)

    file_path = tools_dir / f"{name.lower().replace(' ', '_')}.py"
    with file_path.open('w') as f:
        f.write("from cognitrix.tools.tool import tool\n\n")
        f.write(f"@tool(category='{category}')\n")
        f.write(f"def {name.lower().replace(' ', '_')}(*args, **kwargs):\n")
        f.write(f"    \"\"\"{description}\"\"\"\n")
        f.write(f"    {function_code.strip()}\n")

# Function to load saved tools
# def load_saved_tools():
#     """Load all saved tools from the custom_tools directory."""
#     tools_dir = Path("custom_tools")
#     if not tools_dir.exists():
#         return

#     for file_path in tools_dir.glob("*.json"):
#         with file_path.open('r') as f:
#             tool_info = json.load(f)

#         create_tool(**tool_info)

#     for file_path in tools_dir.glob("*.py"):
#         module_name = file_path.stem
#         spec = importlib.util.spec_from_file_location(module_name, file_path)
#         module = importlib.util.module_from_spec(spec)
#         spec.loader.exec_module(module)

#         # Add the loaded tool to the global namespace
#         for name, obj in module.__dict__.items():
#             if callable(obj) and hasattr(obj, '_is_tool'):
#                 globals()[name] = obj

