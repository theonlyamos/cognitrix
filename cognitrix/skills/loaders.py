"""Skill loaders — pluggable backends for discovering skills from various sources.

Supports:
- LocalDirectoryLoader: ~/.agents/skills/ (global) and .agents/skills/ (project-scoped)
- DatabaseLoader: skills persisted in the Cognitrix database
- RemoteRegistryLoader: GitHub-based skill registries
"""

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from cognitrix.skills.models import SkillManifest, Skill
from cognitrix.skills.parser import SkillParser, SkillParseError

logger = logging.getLogger('cognitrix.log')

FRONTMATTER_HEADER = re.compile(r'^---\s*\n', re.MULTILINE)
MAX_REMOTE_SIZE = 500_000


class SkillLoader(ABC):
    """Base class for skill loading strategies."""

    @abstractmethod
    async def discover(self) -> list[SkillManifest]:
        """Return all skills available from this source."""

    @abstractmethod
    async def load(self, name: str) -> SkillManifest | None:
        """Load a specific skill by name."""

    @abstractmethod
    async def exists(self, name: str) -> bool:
        """Check if a skill exists in this source."""


class LocalDirectoryLoader(SkillLoader):
    """Loads skills from filesystem directories.

    Scans each directory for subdirectories containing a SKILL.md file.
    Supports:
    - Global skills: ~/.agents/skills/
    - Project skills: .agents/skills/ (relative to project root)
    - Built-in skills: cognitrix/skills/builtin/
    """

    def __init__(self, skill_dirs: list[Path]):
        self.skill_dirs = skill_dirs
        self._parser = SkillParser()

    async def discover(self) -> list[SkillManifest]:
        """Walk each directory and find SKILL.md files."""

        async def scan_dir(skill_dir: Path) -> list[SkillManifest]:
            if not skill_dir.exists():
                return []
            return await asyncio.to_thread(self._scan_dir_sync, skill_dir)

        results = await asyncio.gather(*[scan_dir(d) for d in self.skill_dirs])
        skills: list[SkillManifest] = []
        for result in results:
            skills.extend(result)
        return skills

    def _scan_dir_sync(self, skill_dir: Path) -> list[SkillManifest]:
        """Synchronous directory scan (runs in thread pool)."""
        skills: list[SkillManifest] = []
        try:
            for child in skill_dir.iterdir():
                if not child.is_dir():
                    continue
                skill_file = child / 'SKILL.md'
                if skill_file.exists():
                    try:
                        manifest = self._parser.parse_file(skill_file)
                        skills.append(manifest)
                    except SkillParseError as e:
                        logger.warning(f"Skipping invalid skill at {skill_file}: {e}")
                    except Exception as e:
                        logger.error(f"Error loading skill from {skill_file}: {e}")
        except PermissionError as e:
            logger.warning(f"Permission denied accessing {skill_dir}: {e}")
        return skills

    async def load(self, name: str) -> SkillManifest | None:
        """Load a specific skill by name from any configured directory."""
        for skill_dir in self.skill_dirs:
            skill_file = skill_dir / name / 'SKILL.md'
            if skill_file.exists():
                try:
                    return self._parser.parse_file(skill_file)
                except SkillParseError as e:
                    logger.warning(f"Failed to parse skill '{name}': {e}")
        return None

    async def exists(self, name: str) -> bool:
        """Check if a skill directory exists in any configured directory."""
        return any(
            (skill_dir / name / 'SKILL.md').exists()
            for skill_dir in self.skill_dirs
        )


class DatabaseLoader(SkillLoader):
    """Loads skills persisted as Skill ORM records."""

    def __init__(self):
        self._parser = SkillParser()

    async def discover(self) -> list[SkillManifest]:
        """Query all enabled Skill records."""
        skills: list[SkillManifest] = []
        try:
            records = Skill.find({}) or []
            for record in records:
                if not record.enabled:
                    continue
                if record.source_path:
                    skill_file = Path(record.source_path) / 'SKILL.md'
                    if skill_file.exists():
                        try:
                            manifest = self._parser.parse_file(skill_file)
                            skills.append(manifest)
                        except SkillParseError:
                            pass
        except Exception as e:
            logger.warning(f"DatabaseLoader.discover failed: {e}")
        return skills

    async def load(self, name: str) -> SkillManifest | None:
        """Load a skill from database by name."""
        try:
            record = Skill.find_one({'name': name})
            if record and record.enabled and record.source_path:
                skill_file = Path(record.source_path) / 'SKILL.md'
                if skill_file.exists():
                    return self._parser.parse_file(skill_file)
        except Exception as e:
            logger.warning(f"DatabaseLoader.load('{name}') failed: {e}")
        return None

    async def exists(self, name: str) -> bool:
        """Check if a skill exists in the database."""
        try:
            record = Skill.find_one({'name': name})
            return record is not None and record.enabled
        except Exception:
            return False


class RemoteRegistryLoader(SkillLoader):
    """Loads skills from a GitHub-based remote registry.

    Registry format:
    - GitHub repo with a skills/ directory
    - Each subdirectory is a skill containing a SKILL.md
    - index.json at root lists available skills with versions and descriptions

    Default registry: https://github.com/theonlyamos/cognitrix-skills
    """

    def __init__(self, registry_url: str, cache_dir: Path):
        self.registry_url = registry_url.rstrip('/')
        self.cache_dir = cache_dir
        self._parser = SkillParser()
        self._index: dict[str, Any] | None = None
        self._session: Any = None
        self._index_lock = asyncio.Lock()

    async def _get_session(self) -> 'aiohttp.ClientSession':
        """Get or create a reusable aiohttp session with connection pooling."""
        if self._session is None or self._session.closed:
            import aiohttp
            connector = aiohttp.TCPConnector(
                limit=10,
                ttl_dns_cache=300,
                ssl=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def discover(self) -> list[SkillManifest]:
        """Fetch index.json and return metadata for available skills."""
        index = await self._fetch_index()
        if not index:
            return []

        skills: list[SkillManifest] = []
        for entry in index.get('skills', []):
            # Build a lightweight manifest from index metadata
            manifest = SkillManifest(
                name=entry.get('name', ''),
                description=entry.get('description', ''),
                version=entry.get('version', '1.0.0'),
                author=entry.get('author', ''),
                tags=entry.get('tags', []),
                category=entry.get('category', 'general'),
                source_url=f"{self.registry_url}/tree/main/skills/{entry.get('name', '')}",
            )
            skills.append(manifest)
        return skills

    async def load(self, name: str) -> SkillManifest | None:
        """Download and parse a skill from the registry."""
        cached = self.cache_dir / name / 'SKILL.md'
        if cached.exists():
            try:
                return self._parser.parse_file(cached)
            except SkillParseError:
                pass

        # Download from registry
        content = await self._fetch_skill_content(name)
        if not content:
            return None

        # Cache it
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(content, encoding='utf-8')

        try:
            return self._parser.parse(content, source_path=str(cached.parent))
        except SkillParseError as e:
            logger.warning(f"Remote skill '{name}' parse failed: {e}")
            return None

    async def exists(self, name: str) -> bool:
        """Check if a skill exists in the remote registry."""
        index = await self._fetch_index()
        if not index:
            return False
        return any(s.get('name') == name for s in index.get('skills', []))

    async def install(self, name: str, target_dir: Path) -> SkillManifest | None:
        """Download a skill and install it to the target directory."""
        content = await self._fetch_skill_content(name)
        if not content:
            logger.error(f"Could not fetch skill '{name}' from registry")
            return None

        # Write to target
        skill_dir = target_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / 'SKILL.md').write_text(content, encoding='utf-8')

        try:
            manifest = self._parser.parse(content, source_path=str(skill_dir))
            # Also persist in database
            Skill(
                name=manifest.name,
                description=manifest.description,
                version=manifest.version,
                author=manifest.author,
                tags=manifest.tags,
                category=manifest.category,
                source_path=str(skill_dir),
                source_url=f"{self.registry_url}/tree/main/skills/{name}",
            ).save()
            return manifest
        except Exception as e:
            logger.error(f"Failed to install skill '{name}': {e}")
            return None

    async def check_updates(self, name: str, current_version: str) -> str | None:
        """Check if a newer version exists; return new version or None."""
        index = await self._fetch_index()
        if not index:
            return None
        for entry in index.get('skills', []):
            if entry.get('name') == name:
                remote_version = entry.get('version', '1.0.0')
                if remote_version != current_version:
                    return remote_version
        return None

    # ── Private helpers ──

    async def _fetch_index(self) -> dict[str, Any] | None:
        """Fetch and cache the registry index.json."""
        if self._index is not None:
            return self._index

        async with self._index_lock:
            if self._index is not None:
                return self._index

            try:
                import aiohttp
                raw_url = self._to_raw_url('index.json')
                session = await self._get_session()
                async with session.get(raw_url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if len(text) > MAX_REMOTE_SIZE:
                            logger.warning(f"Registry index too large: {len(text)} bytes")
                            return None
                        self._index = json.loads(text)
                        return self._index
                    else:
                        logger.warning(f"Registry index fetch failed: HTTP {resp.status}")
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in registry index: {e}")
            except Exception as e:
                logger.warning(f"Failed to fetch registry index: {e}")
        return None

    async def _fetch_skill_content(self, name: str) -> str | None:
        """Fetch SKILL.md content for a specific skill."""
        try:
            import aiohttp
            raw_url = self._to_raw_url(f'skills/{name}/SKILL.md')
            session = await self._get_session()
            async with session.get(raw_url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if len(text) > MAX_REMOTE_SIZE:
                        logger.warning(f"Skill '{name}' content too large: {len(text)} bytes")
                        return None
                    if not self._validate_skill_content(text):
                        logger.warning(f"Skill '{name}' content validation failed")
                        return None
                    return text
                else:
                    logger.warning(f"Skill '{name}' not found in registry: HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to fetch skill '{name}': {e}")
        return None

    def _validate_skill_content(self, content: str) -> bool:
        """Validate remote skill content before writing to disk."""
        if not content or len(content) < 10:
            return False
        if not content.strip().startswith('---'):
            return False
        if 'name:' not in content.lower():
            return False
        if 'description:' not in content.lower():
            return False
        return True

    def _to_raw_url(self, path: str) -> str:
        """Convert a GitHub repo URL to a raw content URL."""
        # https://github.com/user/repo → https://raw.githubusercontent.com/user/repo/main/path
        if 'github.com' in self.registry_url:
            raw_base = self.registry_url.replace('github.com', 'raw.githubusercontent.com')
            return f"{raw_base}/main/{path}"
        # Fallback: assume it's already a direct URL
        return f"{self.registry_url}/{path}"
