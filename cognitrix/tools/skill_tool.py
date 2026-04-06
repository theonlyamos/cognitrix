"""use_skill meta-tool — allows agents to invoke skills programmatically."""

import logging

from cognitrix.tools.tool import tool
from cognitrix.skills.models import RiskLevel

logger = logging.getLogger('cognitrix.log')


@tool(category='skills')
async def use_skill(
    skill_name: str,
    arguments: str | list | dict = "",
    risk_level: str | None = None,
    context: str = "same",
    **kwargs: dict
) -> str:
    """Execute a registered skill by name.

    Use this tool to invoke a skill when you detect that the user's request
    matches a skill's description. Skills are reusable workflows that follow
    specific patterns to produce high-quality output.

    Args:
        skill_name: Name of the skill to execute (e.g. 'research', 'code-review')
        arguments: Arguments to pass to the skill. Supports multiple formats:
                   - dict (RECOMMENDED): Structured arguments like {"file_path": "file.pdf", "depth": "medium"}
                   - list: Positional arguments like ["file.pdf", "medium"]
                   - str: Space-separated string like "file.pdf medium"
        risk_level: Optional risk level override (low, medium, high). If provided,
                    overrides the skill's default risk level for approval purposes.
        context: Execution mode for the skill. Options:
                 - "same" (default): Run in the current conversation, sharing history
                 - "fork": Run in a new sub-agent with its own system prompt, no conversation history
        **kwargs: Alternative to arguments - pass skill arguments as keyword arguments.
                  Example: use_skill(skill_name="summarize-document", file_path="doc.pdf", depth="medium")

    IMPORTANT: Make ONE call and wait for the result. Do NOT make parallel calls.
    """
    # Validate context parameter
    if context not in ("same", "fork"):
        return f"Invalid context: '{context}'. Must be 'same' or 'fork'."

    # Extract parent from kwargs (auto-injected for sub-agent tools)
    parent = kwargs.pop('parent', None)

    from cognitrix.skills.manager import get_skill_manager
    from cognitrix.skills.executor import SkillExecutor
    from cognitrix.skills.models import SkillEventType

    manager = get_skill_manager()
    manifest = await manager.get_skill(skill_name)

    if not manifest:
        available = [s.name for s in manager.list_skills_sync()]
        return f"Skill '{skill_name}' not found. Available skills: {available}"

    # Validate kwargs against skill's args definition
    if manifest.args:
        # Check if we have arguments in any form
        has_arguments = bool(arguments) or bool(kwargs)
        
        for arg_def in manifest.args:
            # Check kwargs
            if arg_def.name in kwargs:
                if not isinstance(kwargs[arg_def.name], str):
                    return f"Invalid argument '{arg_def.name}': expected string, got {type(kwargs[arg_def.name]).__name__}"
            # Check required args
            elif arg_def.required:
                if not has_arguments:
                    return f"Missing required argument '{arg_def.name}' for skill '{skill_name}'"

    # Create a copy of the manifest to avoid mutating the cached original
    manifest = manifest.model_copy(deep=True)

    # Override risk level if parameter provided (falls back to LOW on invalid input)
    if risk_level:
        try:
            manifest.safety.risk_level = RiskLevel(risk_level.lower())
            logger.info(f"Risk level overridden to '{risk_level}' for skill '{skill_name}'")
        except ValueError:
            logger.warning(f"Invalid risk_level '{risk_level}' for skill '{skill_name}', defaulting to LOW")
            manifest.safety.risk_level = RiskLevel.LOW

    # Check if agent is allowed to invoke this skill
    if manifest.disable_model_invocation:
        return f"Skill '{skill_name}' can only be invoked manually by the user (disable-model-invocation: true)"

    # Set context mode
    manifest.context = context

    # Get agent's LLM and manager - always prefer parent's if available
    from cognitrix.agents.base import AgentManager

    if parent and hasattr(parent, 'llm'):
        llm = parent.llm
        agent_manager = AgentManager(parent)
    else:
        # Fallback: create minimal context (for CLI usage without agent)
        from cognitrix.providers.base import LLM
        llm = LLM.load_llm('openrouter')
        if not llm:
            return "Failed to initialise LLM for skill execution"
        from cognitrix.models import Agent
        fallback_agent = Agent(
            name="skill-executor",
            llm=llm,
            system_prompt="You are a skill execution agent that runs skills on behalf of the user.",
        )
        agent_manager = AgentManager(fallback_agent)

    executor = SkillExecutor(agent_manager=agent_manager, llm=llm)

    # Execute and collect result
    result_parts: list[str] = []
    
    # Use kwargs as skill_args (structured arguments), or convert arguments if provided
    # Priority: kwargs > arguments (kwargs takes precedence if both provided)
    if kwargs:
        skill_args = kwargs
    else:
        skill_args = None
    
    async for event in executor.execute(manifest, arguments, skill_args=skill_args):
        if event.type == SkillEventType.SKILL_PROGRESS:
            if event.data:
                result_parts.append(str(event.data))
        elif event.type == SkillEventType.SKILL_ERROR:
            return f"Skill execution failed: {event.data}"

    return ''.join(result_parts) if result_parts else "Skill completed with no output"
