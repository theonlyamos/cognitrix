"""SKILL.md parser.

Parses Markdown files with YAML frontmatter into SkillManifest objects.
Format:
  ---
  name: my-skill
  description: What the skill does
  [optional frontmatter fields]
  ---
  
  Free-form markdown instructions (the body)
"""

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from cognitrix.skills.models import SkillManifest, SkillSafety, RiskLevel, SkillArg

logger = logging.getLogger('cognitrix.log')

# Frontmatter keys that use hyphens in YAML but underscores in Python
HYPHEN_TO_UNDERSCORE = {
    'disable-model-invocation': 'disable_model_invocation',
    'user-invocable': 'user_invocable',
    'allowed-tools': 'allowed_tools',
    'argument-hint': 'argument_hint',
    'args': 'args',
    'risk-level': 'risk_level',
    'requires-approval': 'requires_approval',
    'source-path': 'source_path',
    'source-url': 'source_url',
}


class SkillParseError(Exception):
    """Raised when a SKILL.md file cannot be parsed."""
    pass


class SkillParser:
    """Parses SKILL.md files into SkillManifest objects."""

    _FRONTMATTER_PATTERN = re.compile(
        r'^---\s*\n(.*?)\n---\s*\n?',
        re.DOTALL
    )

    def parse(self, content: str, source_path: str | None = None) -> SkillManifest:
        """Parse raw SKILL.md content into a SkillManifest.

        Args:
            content:     Raw file content
            source_path: Optional filesystem path for source tracking

        Returns:
            Parsed SkillManifest

        Raises:
            SkillParseError: If content is invalid
        """
        frontmatter_raw, body = self._split_frontmatter(content)
        frontmatter = self._parse_frontmatter(frontmatter_raw)

        # Validate required fields
        if 'name' not in frontmatter:
            raise SkillParseError("SKILL.md missing required 'name' in frontmatter")
        if 'description' not in frontmatter:
            raise SkillParseError("SKILL.md missing required 'description' in frontmatter")

        # Validate name format
        name = frontmatter['name']
        if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', name):
            raise SkillParseError(
                f"Skill name '{name}' is invalid. Must be lowercase alphanumeric "
                f"with hyphens, not starting/ending with hyphens."
            )
        if len(name) > 64:
            raise SkillParseError(f"Skill name '{name}' exceeds 64 character limit")

        # Normalize hyphenated keys to underscored
        normalised = self._normalise_keys(frontmatter)

        # Handle string allowed-tools: split on whitespace, preserving parentheses groups
        if 'allowed_tools' in normalised:
            value = normalised['allowed_tools']
            if isinstance(value, str):
                parts = re.split(r'\s+(?![^()]*\))', value)
                normalised['allowed_tools'] = parts

        # Extract safety sub-object
        safety_data = normalised.pop('safety', None)
        safety = self._parse_safety(safety_data) if safety_data else SkillSafety()

        # Extract args sub-object
        args_data = normalised.pop('args', None)
        args = self._parse_args(args_data) if args_data else None

        # Build manifest
        manifest = SkillManifest(
            body=body.strip(),
            safety=safety,
            args=args,
            source_path=source_path,
            **normalised,
        )
        return manifest

    def parse_file(self, path: Path) -> SkillManifest:
        """Read and parse a SKILL.md file from disk.

        Args:
            path: Path to the SKILL.md file

        Returns:
            Parsed SkillManifest
        """
        if not path.exists():
            raise SkillParseError(f"SKILL.md not found: {path}")

        content = path.read_text(encoding='utf-8')
        skill_dir = str(path.parent)
        return self.parse(content, source_path=skill_dir)

    def serialize(self, manifest: SkillManifest) -> str:
        """Serialize a SkillManifest back to SKILL.md format.

        Args:
            manifest: The skill to serialize

        Returns:
            SKILL.md content string
        """
        # Build frontmatter dict
        fm: dict[str, Any] = {
            'name': manifest.name,
            'description': manifest.description,
        }

        # Optional fields (only include if non-default)
        if manifest.version != "1.0.0":
            fm['version'] = manifest.version
        if manifest.author:
            fm['author'] = manifest.author
        if manifest.tags:
            fm['tags'] = manifest.tags
        if manifest.category != "general":
            fm['category'] = manifest.category
        if manifest.disable_model_invocation:
            fm['disable-model-invocation'] = True
        if not manifest.user_invocable:
            fm['user-invocable'] = False
        if manifest.allowed_tools is not None:
            fm['allowed-tools'] = manifest.allowed_tools
        if manifest.context:
            fm['context'] = manifest.context
        if manifest.agent:
            fm['agent'] = manifest.agent
        if manifest.effort:
            fm['effort'] = manifest.effort
        if manifest.argument_hint:
            fm['argument-hint'] = manifest.argument_hint
        if manifest.args:
            fm['args'] = [
                {
                    'name': a.name,
                    'description': a.description,
                    'required': a.required,
                }
                | ({'default': a.default} if a.default is not None else {})
                for a in manifest.args
            ]
        if manifest.safety.risk_level != RiskLevel.LOW or manifest.safety.requires_approval:
            fm['safety'] = {
                'risk_level': manifest.safety.risk_level.value,
                'requires_approval': manifest.safety.requires_approval,
            }

        frontmatter_str = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
        return f"---\n{frontmatter_str}\n---\n\n{manifest.body}\n"

    # ── Internal methods ──

    def _split_frontmatter(self, content: str) -> tuple[str, str]:
        """Split content into frontmatter YAML and body markdown."""
        match = self._FRONTMATTER_PATTERN.match(content)
        if not match:
            raise SkillParseError(
                "SKILL.md must start with YAML frontmatter (--- delimiters)"
            )
        frontmatter_raw = match.group(1)
        body = content[match.end():]
        return frontmatter_raw, body

    def _parse_frontmatter(self, raw: str) -> dict[str, Any]:
        """Parse YAML frontmatter string."""
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise SkillParseError(f"Invalid YAML in frontmatter: {e}")

        if not isinstance(data, dict):
            raise SkillParseError("Frontmatter must be a YAML mapping")

        return data

    def _normalise_keys(self, data: dict[str, Any]) -> dict[str, Any]:
        """Convert hyphenated YAML keys to underscored Python names."""
        result = {}
        for key, value in data.items():
            py_key = HYPHEN_TO_UNDERSCORE.get(key, key.replace('-', '_'))
            result[py_key] = value
        return result

    def _parse_safety(self, data: Any) -> SkillSafety:
        """Parse safety sub-object from frontmatter."""
        if isinstance(data, dict):
            normalised = self._normalise_keys(data)
            risk = normalised.get('risk_level', 'low')
            if isinstance(risk, str):
                try:
                    risk = RiskLevel(risk)
                except ValueError:
                    logger.warning(f"Unknown risk_level '{risk}', defaulting to 'low'")
                    risk = RiskLevel.LOW
            return SkillSafety(
                risk_level=risk,
                requires_approval=normalised.get('requires_approval', False),
            )
        return SkillSafety()

    def _parse_args(self, data: Any) -> list[SkillArg] | None:
        """Parse args list from frontmatter."""
        if isinstance(data, list):
            args = []
            for item in data:
                if isinstance(item, dict):
                    args.append(SkillArg(
                        name=item.get('name', ''),
                        description=item.get('description', ''),
                        required=item.get('required', False),
                        default=item.get('default'),
                    ))
            return args
        return None
