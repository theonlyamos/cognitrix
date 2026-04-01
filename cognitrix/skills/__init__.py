"""Skill system for Cognitrix — reusable prompt-based instruction sets."""

from cognitrix.skills.models import (
    Skill,
    SkillDependencies,
    SkillEvent,
    SkillEventType,
    SkillManifest,
    SkillSafety,
    RiskLevel,
)
from cognitrix.skills.parser import SkillParser
from cognitrix.skills.manager import SkillManager, get_skill_manager

__all__ = [
    "Skill",
    "SkillEvent",
    "SkillEventType",
    "SkillManifest",
    "SkillSafety",
    "RiskLevel",
    "SkillParser",
    "SkillManager",
    "get_skill_manager",
]
