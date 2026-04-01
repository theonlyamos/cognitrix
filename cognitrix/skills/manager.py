"""SkillManager — central registry for skill discovery, CRUD, search, and validation."""

import functools
import logging
import shutil
from pathlib import Path
from typing import Any

from cognitrix.skills.models import Skill, SkillManifest
from cognitrix.skills.parser import SkillParser, SkillParseError
from cognitrix.skills.loaders import (
    SkillLoader,
    LocalDirectoryLoader,
    DatabaseLoader,
    RemoteRegistryLoader,
)

logger = logging.getLogger('cognitrix.log')

# Module-level singleton
_skill_manager: 'SkillManager | None' = None


def get_skill_manager() -> 'SkillManager':
    """Get or create the global SkillManager singleton."""
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
        _skill_manager.setup_default_loaders()
    return _skill_manager


class SkillManager:
    """Central skill registry.

    Manages skill discovery, CRUD, search, and validation
    through pluggable SkillLoader backends.
    """

    def __init__(self):
        self.loaders: list[SkillLoader] = []
        self._cache: dict[str, SkillManifest] = {}
        self._parser = SkillParser()

    def setup_default_loaders(self):
        """Register the default set of loaders."""
        from cognitrix.config import BASE_DIR

        builtin_dir = BASE_DIR / 'skills' / 'builtin'
        global_dir = Path.home() / '.agents' / 'skills'
        project_dir = Path.cwd() / '.agents' / 'skills'
        cache_dir = global_dir / '.cache'

        # Ensure directories exist
        global_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Filesystem loaders (project-scoped first, then global, then built-in)
        dirs: list[Path] = []
        if project_dir.exists():
            dirs.append(project_dir)
        dirs.append(global_dir)
        if builtin_dir.exists():
            dirs.append(builtin_dir)

        self.register_loader(LocalDirectoryLoader(dirs))

        # Database loader
        self.register_loader(DatabaseLoader())

        # Remote registry loader
        registry_url = 'https://github.com/theonlyamos/cognitrix-skills'
        self.register_loader(RemoteRegistryLoader(registry_url, cache_dir))

    # ── Loader management ──

    def register_loader(self, loader: SkillLoader):
        """Register a skill loading backend."""
        self.loaders.append(loader)

    # ── Discovery ──

    async def discover_all(self) -> list[SkillManifest]:
        """Scan all loaders and return all available skills."""
        skills: list[SkillManifest] = []
        seen_names: set[str] = set()

        for loader in self.loaders:
            try:
                discovered = await loader.discover()
                for skill in discovered:
                    if skill.name not in seen_names:
                        skills.append(skill)
                        seen_names.add(skill.name)
                        self._cache[skill.name] = skill
            except Exception as e:
                logger.warning(f"Loader {loader.__class__.__name__} failed: {e}")

        return skills

    async def refresh_cache(self):
        """Re-scan loaders and update the in-memory cache."""
        self._cache.clear()
        self.list_skills_sync.cache_clear()
        self.get_skill_summaries.cache_clear()
        await self.discover_all()

    # ── CRUD ──

    async def get_skill(self, name: str) -> SkillManifest | None:
        """Retrieve a skill by name (cache-first, then scan loaders)."""
        if name in self._cache:
            return self._cache[name]

        for loader in self.loaders:
            try:
                skill = await loader.load(name)
                if skill:
                    self._cache[name] = skill
                    return skill
            except Exception as e:
                logger.warning(f"Loader {loader.__class__.__name__}.load('{name}') failed: {e}")

        return None

    async def list_skills(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> list[SkillManifest]:
        """List skills with optional filtering."""
        if not self._cache:
            await self.discover_all()

        skills = list(self._cache.values())

        if category:
            skills = [s for s in skills if s.category == category]
        if tags:
            tag_set = set(tags)
            skills = [s for s in skills if tag_set.intersection(s.tags)]

        return sorted(skills, key=lambda s: s.name)

    async def install_skill(self, source: str) -> SkillManifest | None:
        """Install a skill from a local path, URL, or registry name.

        Detects source type:
        - Local path → copy to ~/.agents/skills/
        - URL (http) → fetch via RemoteRegistryLoader
        - Registry name → resolve from remote registry
        """
        global_dir = Path.home() / '.agents' / 'skills'

        # Local directory
        local_path = Path(source)
        if local_path.exists() and local_path.is_dir():
            skill_file = local_path / 'SKILL.md'
            if not skill_file.exists():
                logger.error(f"No SKILL.md found in {source}")
                return None

            manifest = self._parser.parse_file(skill_file)
            target = global_dir / manifest.name
            if target.exists():
                logger.warning(f"Skill '{manifest.name}' already installed, overwriting")
                shutil.rmtree(target)

            shutil.copytree(local_path, target)
            manifest.source_path = str(target)

            # Persist in DB
            Skill(
                name=manifest.name,
                description=manifest.description,
                version=manifest.version,
                author=manifest.author,
                tags=manifest.tags,
                category=manifest.category,
                source_path=str(target),
            ).save()

            self._cache[manifest.name] = manifest
            return manifest

        # Remote: URL or registry name
        for loader in self.loaders:
            if isinstance(loader, RemoteRegistryLoader):
                manifest = await loader.install(source, global_dir)
                if manifest:
                    self._cache[manifest.name] = manifest
                    return manifest

        logger.error(f"Could not install skill from '{source}'")
        return None

    async def remove_skill(self, name: str) -> bool:
        """Remove a skill from ~/.agents/skills/ and database."""
        global_dir = Path.home() / '.agents' / 'skills'
        skill_dir = global_dir / name

        removed = False

        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            removed = True

        # Remove from database
        try:
            record = Skill.find_one({'name': name})
            if record:
                record.delete()
                removed = True
        except Exception as e:
            logger.warning(f"Failed to remove skill '{name}' from DB: {e}")

        self._cache.pop(name, None)
        return removed

    async def create_skill(
        self,
        name: str,
        description: str,
        body: str,
        project_scope: bool = False,
        **frontmatter: Any,
    ) -> SkillManifest:
        """Create a new skill directory with SKILL.md.

        Args:
            name:          Skill name (slug)
            description:   What the skill does
            body:          Markdown instructions
            project_scope: If True, create in .agents/skills/ (project-scoped)
            **frontmatter: Additional frontmatter fields
        """
        manifest = SkillManifest(
            name=name,
            description=description,
            body=body,
            **frontmatter,
        )

        # Validate
        errors = self.validate_skill(manifest)
        if errors:
            raise SkillParseError(f"Invalid skill: {'; '.join(errors)}")

        # Determine target directory
        if project_scope:
            target_base = Path.cwd() / '.agents' / 'skills'
        else:
            target_base = Path.home() / '.agents' / 'skills'

        target_dir = target_base / name
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write SKILL.md
        content = self._parser.serialize(manifest)
        (target_dir / 'SKILL.md').write_text(content, encoding='utf-8')

        manifest.source_path = str(target_dir)
        self._cache[name] = manifest
        return manifest

    # ── Validation ──

    def validate_skill(self, manifest: SkillManifest) -> list[str]:
        """Validate a manifest. Returns list of error messages (empty = valid)."""
        errors: list[str] = []

        import re
        if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', manifest.name):
            errors.append(
                f"Name '{manifest.name}' must be lowercase alphanumeric with hyphens"
            )
        if len(manifest.name) > 64:
            errors.append(f"Name exceeds 64 characters")
        if not manifest.description:
            errors.append("Description is required")
        if len(manifest.description) > 1024:
            errors.append("Description exceeds 1024 characters")

        return errors

    # ── Search ──

    async def search_skills(self, query: str) -> list[SkillManifest]:
        """Fuzzy-search skills by name, description, and tags."""
        if not self._cache:
            await self.discover_all()

        query_lower = query.lower()
        results: list[tuple[int, SkillManifest]] = []

        for skill in self._cache.values():
            score = 0
            if query_lower in skill.name:
                score += 10
            if query_lower in skill.description.lower():
                score += 5
            if any(query_lower in tag for tag in skill.tags):
                score += 3
            if query_lower in skill.category:
                score += 2
            if score > 0:
                results.append((score, skill))

        results.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in results]

    # ── Sync convenience ──

    @functools.lru_cache(maxsize=1)
    def list_skills_sync(self) -> tuple[SkillManifest, ...]:
        """Return cached skill list synchronously (for prompt building)."""
        return tuple(sorted(self._cache.values(), key=lambda s: s.name))

    @functools.lru_cache(maxsize=1)
    def get_skill_summaries(self) -> tuple[dict[str, str], ...]:
        """Return summaries for system prompt injection (cached)."""
        return tuple(
            {
                'name': s.name,
                'description': s.description,
                'user_invocable': str(s.user_invocable),
                'argument_hint': s.argument_hint or '',
            }
            for s in self.list_skills_sync()
        )
