"""load_skill meta-tool — loads skill instructions into agent context."""

import logging

from cognitrix.tools.tool import tool

logger = logging.getLogger('cognitrix.log')

# Tool name aliases for allowed-tools resolution
TOOL_ALIASES = {
    'bash': 'Bash',
    'shell': 'Bash',
    'read': 'Read',
    'write': 'Write',
    'glob': 'Glob',
    'grep': 'Grep',
    'list_dir': 'list_dir',
    'ls': 'list_dir',
}


@tool(name='load_skill', category='skills')
async def load_skill(skill_name: str, context: str = "same") -> str:
    """Load a skill's instructions into context.

    Use this tool to invoke a skill when you detect that the user's request
    matches a skill's description.
    
    For context="same": Returns the skill's instructions for you to follow.
    For context="fork": Creates a sub-agent with the skill's allowed tools and executes it.

    Args:
        skill_name: Name of the skill to execute (e.g. 'summarize-document')
        context: "same" (default) - follow instructions in current conversation
                 "fork" - create sub-agent with skill's allowed tools
    
    Returns: 
        For "same": The rendered SKILL.md content with instructions to follow.
        For "fork": The sub-agent's execution result.
    """
    from cognitrix.skills.manager import get_skill_manager
    from cognitrix.agents.base import AgentManager
    from cognitrix.tools.base import ToolManager

    if context not in ("same", "fork"):
        return f"Invalid context: '{context}'. Must be 'same' or 'fork'."

    # Extract parent from kwargs (auto-injected for sub-agent tools)
    kwargs = {}
    parent = kwargs.pop('parent', None)

    manager = get_skill_manager()
    manifest = await manager.get_skill(skill_name)

    if not manifest:
        available = [s.name for s in manager.list_skills_sync()]
        return f"Skill '{skill_name}' not found. Available skills: {available}"

    # Render skill content - resolve environment variables
    skill_content = _render_skill(manifest)

    if context == "same":
        # Return the skill instructions for the agent to follow
        return skill_content

    # Fork context: create sub-agent with allowed tools
    return await _execute_forked(
        manifest=manifest,
        skill_content=skill_content,
        parent=parent,
    )


def _render_skill(manifest) -> str:
    """Render skill content - resolve environment variables."""
    replacements = {
        '${COGNITRIX_SKILL_DIR}': manifest.source_path or '',
    }

    result = manifest.body
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


async def _execute_forked(manifest, skill_content: str, parent) -> str:
    """Execute skill in a forked sub-agent with allowed tools."""
    from cognitrix.agents.base import AgentManager
    from cognitrix.models import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.utils.llm_response import LLMResponse

    # Get the parent's LLM
    if parent and hasattr(parent, 'llm'):
        llm = parent.llm
    else:
        llm = LLM.load_llm('openrouter')
        if not llm:
            return "Failed to initialise LLM for skill execution"

    # Get allowed tools from manifest
    allowed_tools = manifest.allowed_tools or []
    
    # Resolve allowed tools
    allowed_set, restriction_map = _resolve_allowed_tools(allowed_tools)
    
    # Get all tools and filter to allowed only
    all_tools = ToolManager.list_all_tools()
    if allowed_set:
        tools = [t for t in all_tools if t.name in allowed_set]
    else:
        tools = all_tools

    # Create sub-agent with skill content as system prompt
    sub_agent = Agent(
        name=f"skill-{manifest.name}",
        llm=llm,
        system_prompt=skill_content,
        tools=tools,
    )
    sub_manager = AgentManager(sub_agent)

    # Get parent's last message to use as sub-agent's input
    user_input = _get_parent_last_message(parent)

    # Execute sub-agent (single turn)
    try:
        result_parts = []
        async for response in sub_manager.generate(user_input):
            if hasattr(response, 'result'):
                result_parts.append(response.result)
            elif hasattr(response, 'content'):
                result_parts.append(response.content)
        
        result = ''.join(result_parts)
        return result if result else "Skill completed with no output"
    except Exception as e:
        return f"Skill execution failed: {str(e)}"


def _resolve_allowed_tools(allowed_tools: list[str]) -> tuple[set[str], dict[str, list[str]]]:
    """Resolve tool aliases and extract restrictions.
    
    Parses strings like 'Bash(git *)' into:
    - allowed_set: {'Bash'} (native tool names)
    - restriction_map: {'Bash': ['git', '*']} (glob patterns)
    """
    import re
    tool_pattern = re.compile(r'^([^\(]+)(?:\((.*)\))?$')

    allowed_set: set[str] = set()
    restriction_map: dict[str, list[str]] = {}

    for tool_spec in allowed_tools:
        match = tool_pattern.match(tool_spec.strip())
        if not match:
            continue

        base = match.group(1).strip().lower()
        restriction = match.group(2) or ''

        resolved = TOOL_ALIASES.get(base, base.title())
        allowed_set.add(resolved)

        if restriction:
            patterns = restriction.split()
            restriction_map.setdefault(resolved, []).extend(patterns)

    return allowed_set, restriction_map


def _get_parent_last_message(parent) -> str:
    """Get the parent's last user message for sub-agent input."""
    if not parent:
        return ""
    
    # Try to get messages from parent's inbox (incoming messages)
    if hasattr(parent, 'inbox') and parent.inbox:
        for msg in reversed(parent.inbox):
            if msg.sender.lower() in ('user', 'you'):
                return msg.content
    
    # Try parent's response_list (messages the agent responded to)
    if hasattr(parent, 'response_list') and parent.response_list:
        for msg, _ in reversed(parent.response_list):
            if hasattr(msg, 'sender') and msg.sender.lower() in ('user', 'you'):
                return msg.content
            if hasattr(msg, 'content'):
                return msg.content
    
    # Try to get from context manager if available
    if hasattr(parent, 'get_context_manager'):
        try:
            ctx_mgr = parent.get_context_manager()
            # Try to get recent messages from short-term memory
            if hasattr(ctx_mgr, 'short_term') and hasattr(ctx_mgr.short_term, 'messages'):
                for msg in reversed(ctx_mgr.short_term.messages):
                    if msg.get('role') == 'user':
                        return msg.get('content', '')
        except Exception:
            pass
    
    return ""
