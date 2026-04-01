"""use_skill meta-tool — allows agents to invoke skills programmatically."""

import inspect
import json
import logging
from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolCallResult

logger = logging.getLogger('cognitrix.log')


@tool(category='skills')
async def use_skill(skill_name: str, arguments: str = "", parent: Any = None) -> str:
    """Execute a registered skill by name.

    Use this tool to invoke a skill when you detect that the user's request
    matches a skill's description. Skills are reusable workflows that follow
    specific patterns to produce high-quality output.

    Args:
        skill_name: Name of the skill to execute (e.g. 'web-research', 'code-review')
        arguments: Arguments to pass to the skill (e.g. 'src/auth/login.ts', 'AI agents 2025')
    """
    from cognitrix.skills.manager import get_skill_manager
    from cognitrix.skills.executor import SkillExecutor
    from cognitrix.skills.models import SkillEventType

    manager = get_skill_manager()
    manifest = await manager.get_skill(skill_name)

    if not manifest:
        available = [s.name for s in manager.list_skills_sync()]
        return f"Skill '{skill_name}' not found. Available skills: {available}"

    # Check if agent is allowed to invoke this skill
    if manifest.disable_model_invocation:
        return f"Skill '{skill_name}' can only be invoked manually by the user (disable-model-invocation: true)"

    # Get agent's LLM and manager
    from cognitrix.agents.base import AgentManager
    agent_manager = None
    llm = None

    if parent and hasattr(parent, 'llm'):
        llm = parent.llm
        agent_manager = AgentManager(parent)
    else:
        # Fallback: create minimal context
        from cognitrix.providers.base import LLM
        llm = LLM.load_llm('openrouter')
        if not llm:
            return "Failed to initialise LLM for skill execution"
        from cognitrix.models import Agent
        fallback_agent = Agent(name="skill-executor", llm=llm)
        agent_manager = AgentManager(fallback_agent)

    executor = SkillExecutor(agent_manager=agent_manager, llm=llm)

    # Execute and collect result
    result_parts: list[str] = []
    async for event in executor.execute(manifest, arguments):
        if event.type == SkillEventType.SKILL_PROGRESS:
            if event.data:
                result_parts.append(str(event.data))
        elif event.type == SkillEventType.SKILL_ERROR:
            return f"Skill execution failed: {event.data}"

    return ''.join(result_parts) if result_parts else "Skill completed with no output"
