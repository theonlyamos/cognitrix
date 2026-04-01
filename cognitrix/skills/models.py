"""Skill data models.

Follows the Anthropic Claude Code skill architecture:
- Skills are prompt-based instruction sets (SKILL.md)
- Minimal YAML frontmatter + free-form Markdown body
- Agent-driven execution, not declarative step DAGs
"""

import uuid
from enum import Enum
from typing import Any

from odbms import Model
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SkillEventType(str, Enum):
    """Events emitted during streaming skill execution."""
    SKILL_START = "skill_start"
    SKILL_CONTEXT_INJECTED = "skill_context_injected"
    SKILL_PROMPT_SENT = "skill_prompt_sent"
    SKILL_PROGRESS = "skill_progress"
    SKILL_COMPLETE = "skill_complete"
    SKILL_ERROR = "skill_error"


class SkillDependencies(BaseModel):
    """Dependencies required by a skill's scripts."""
    pip: list[str] = Field(default_factory=list)     # e.g. ["pymupdf", "pandas"]
    system: list[str] = Field(default_factory=list)  # e.g. ["ffmpeg", "git"]


class SkillSafety(BaseModel):
    """Safety configuration for a skill."""
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False


class SkillManifest(BaseModel):
    """Parsed representation of a SKILL.md file.

    The frontmatter provides metadata and control flags.
    The body provides the actual instructions injected into the agent's context.
    """
    # Required frontmatter
    name: str
    description: str

    # Body (free-form markdown instructions)
    body: str = ""

    # Control flags (Anthropic-style)
    disable_model_invocation: bool = False
    user_invocable: bool = True
    allowed_tools: list[str] | None = None  # None = all tools
    context: str | None = None              # "fork" or None (same context)
    agent: str | None = None                # agent name for forked context
    effort: str | None = None               # "low" | "medium" | "high" | "max"
    argument_hint: str | None = None        # e.g. "<topic> [depth]"

    # Metadata
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    version: str = "1.0.0"
    author: str = ""

    # Dependencies (for scripts)
    dependencies: SkillDependencies = Field(default_factory=SkillDependencies)

    # Safety
    safety: SkillSafety = Field(default_factory=SkillSafety)

    # Source tracking
    source_path: str | None = None          # filesystem path to skill dir
    source_url: str | None = None           # remote registry URL


class SkillEvent(BaseModel):
    """Event emitted during streaming skill execution."""
    type: SkillEventType
    skill_name: str
    data: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── ORM Model (persisted to database) ───

class Skill(Model):
    """Persisted skill record in the database."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    source_path: str | None = None
    source_url: str | None = None
    enabled: bool = True
    user_id: str | None = None
