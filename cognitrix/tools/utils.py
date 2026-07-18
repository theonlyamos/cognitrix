import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from odbms import Model
from PIL import Image
from pydantic import BaseModel, Field

from cognitrix.common.process_security import HostProcessMode


@dataclass(frozen=True)
class DocumentCapability:
    """Exact immutable authority to read one identity-pinned managed document."""

    document_id: str
    storage_key: str
    mime_type: str
    filename: str | None
    size_bytes: int
    sha256: str
    tools_root_identity: str
    uploads_identity: str
    directory_identity: str
    file_identity: str


@dataclass(frozen=True)
class ToolExecutionContext:
    """Immutable caller authority bound to one agent turn."""

    user_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    api_key_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    scopes: frozenset[str] | None = None
    allowed_agents: frozenset[str] | None = None
    allowed_teams: frozenset[str] | None = None
    document_capabilities: tuple[DocumentCapability, ...] = ()
    selected_image_artifact_id: str | None = None
    host_process_mode: HostProcessMode = HostProcessMode.DENY

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


def trusted_local_execution_context() -> ToolExecutionContext:
    """Build the host-process capability used only by direct local CLI entry points."""
    return ToolExecutionContext(host_process_mode=HostProcessMode.TRUSTED_LOCAL)


def delegated_execution_context(
    parent: ToolExecutionContext | None = None,
) -> ToolExecutionContext:
    """Retain caller/API policy while dropping all turn-scoped capabilities.

    Constructing a fresh context is intentional: any future capability fields
    default to their non-authorizing value instead of becoming transitively
    delegated by a broad ``dataclasses.replace``.
    """
    source = parent or current_execution_context()
    return ToolExecutionContext(
        user_id=source.user_id,
        api_key_id=source.api_key_id,
        scopes=source.scopes,
        allowed_agents=source.allowed_agents,
        allowed_teams=source.allowed_teams,
        host_process_mode=HostProcessMode.DENY,
    )


@contextmanager
def trusted_local_execution():
    """Bind trusted-local authority around a direct CLI-only operation."""
    token = set_execution_context(trusted_local_execution_context())
    try:
        yield
    finally:
        reset_execution_context(token)


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

    def model_content(self) -> str:
        """Compact, safe summary sent back to the model after a tool call."""
        lines = [self.text]
        lines.extend(
            f'Artifact: {artifact.id} {artifact.mime_type} {artifact.filename or ""}'.rstrip()
            for artifact in self.artifacts
        )
        lines.extend(
            f'Entity: {entity.type} {entity.id} {entity.name}'
            for entity in self.entities
        )
        lines.extend(f'Warning: {warning[:500]}' for warning in self.warnings[:3])
        return '\n'.join(line for line in lines if line)

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

