"""The Memory tool: agents save, update, forget and search what they remember
about the person they work for. See ``cognitrix/memory/saved.py``.

Added to each chat turn (not stored on the agent) when the person has memory
turned on; see ``Session._run_turn``.
"""
from __future__ import annotations

from typing import Any

from cognitrix.memory import saved
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

MEMORY_TOOL_CATEGORY = 'system'


def _owner() -> str | None:
    ctx = current_execution_context()
    if ctx.task_id or ctx.run_id or not ctx.user_id:
        return None
    return str(ctx.user_id)


_ACTIONS = {
    'save': 'save', 'add': 'save', 'remember': 'save', 'create': 'save', 'store': 'save', 'new': 'save',
    'update': 'update', 'edit': 'update', 'change': 'update', 'replace': 'update',
    'forget': 'forget', 'delete': 'forget', 'remove': 'forget',
    'search': 'search', 'find': 'search', 'list': 'search', 'recall': 'search', 'get': 'search',
}
_SCOPES = {
    'global': 'global', 'all': 'global', 'shared': 'global', 'everyone': 'global', 'user': 'global',
    'all_agents': 'global', 'agent': 'agent', 'self': 'agent', 'me': 'agent', 'private': 'agent',
    'this_agent': 'agent', 'agent_only': 'agent', 'local': 'agent',
}


def _line(row: Any) -> str:
    where = 'this agent only' if row.scope == 'agent' else 'global'
    return f'- {row.content} (id {row.id}, {where})'


@tool(category=MEMORY_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def memory(action: str, content: str = '', memory_id: str = '', scope: str = '',
                 query: str = '', *, parent=None) -> ToolOutcome:
    """Your long-term memory about the person you're working for, kept between chats.
    What you already remember is listed in your instructions under "What you remember about the user".

    action:
    - "save": remember one short fact (memory_id is not needed; the app assigns one) (content, at most 300 characters). Save lasting things the person
      tells you about themselves, their work, their preferences and decisions on ongoing projects, or
      anything they ask you to remember. Don't save one-off task details, things you can look up again,
      passwords or keys, or private details about other people.
      scope: "global" (every agent of this person sees it) or "agent" (only you). Leave it empty for your default.
    - "update": replace a memory that changed (memory_id and the new content).
    - "forget": delete a memory (memory_id). Do it when the person asks or a memory turns out wrong.
    - "search": find saved memories that aren't in your instructions (query).
    The person sees a note in the chat each time you save, update or forget something."""
    user_id = _owner()
    if not user_id:
        return ToolOutcome.failure('no_owner', 'Memory works in chats with a signed-in person only.')
    ctx = current_execution_context()
    if ctx.session_id:
        from cognitrix.sessions.base import Session

        current = await Session.get(str(ctx.session_id))
        if current is not None and getattr(current, 'incognito', False):
            return ToolOutcome.failure(
                'incognito', "This is an incognito chat: memory can't be read or changed here.")
    settings = await saved.get_settings(user_id)
    if not settings.enabled:
        return ToolOutcome.failure('memory_off', 'This person has turned memory off in Settings.')

    # Models often say "add" or "user"; read the obvious meaning instead of failing.
    action = _ACTIONS.get(str(action or '').strip().lower().replace(' ', '_'), str(action or '').strip().lower())
    agent_id = str(getattr(parent, 'id', '') or '')
    try:
        if action == 'save':
            if settings.auto_save == 'explicit':
                from cognitrix.safety.action_reviewer import turn_goal

                if not saved.asked_to_remember(turn_goal.get(None)):
                    return ToolOutcome.failure(
                        'not_asked',
                        'This person only wants memories saved when they ask you to remember '
                        'something. Don\'t save this one.')
            chosen = _SCOPES.get(str(scope or '').strip().lower().replace(' ', '_').replace('-', '_'), '')
            if not chosen:
                chosen = 'global' if await saved.agent_saves_global(parent) else 'agent'
            if chosen == 'global' and not saved.agent_reads_global(parent):
                chosen = 'agent'
            if chosen == 'agent' and not agent_id:
                chosen = 'global'
            row, created = await saved.add(
                user_id, content, scope=chosen, agent_id=agent_id,
                source='agent', session_id=str(ctx.session_id or ''))
            if not created:
                return ToolOutcome.success(
                    f'Already remembered (id {row.id}): {row.content}. '
                    'Use action "update" if it needs to change.')
            return ToolOutcome.success(f'Memory saved (id {row.id}, {row.scope}): {row.content}')

        if action in ('update', 'forget'):
            row = await saved.get_memory(user_id, memory_id)
            if row is None or not saved._visible(row, agent_id, saved.agent_reads_global(parent)):
                return ToolOutcome.failure(
                    'not_found', f'No memory with id {memory_id!r}. Use action "search" to find its id.')
            if action == 'forget':
                await saved.delete(row)
                return ToolOutcome.success(f'Memory forgotten (id {row.id}): {row.content}')
            row = await saved.update(row, content=content)
            return ToolOutcome.success(f'Memory updated (id {row.id}, {row.scope}): {row.content}')

        if action == 'search':
            if not str(query or '').strip():
                return ToolOutcome.failure('query_required', 'Give words to search for.')
            rows = await saved.search(user_id, parent, query)
            if not rows:
                return ToolOutcome.success('No saved memories match that.')
            return ToolOutcome.success('\n'.join(_line(r) for r in rows))
    except saved.MemoryError_ as exc:
        return ToolOutcome.failure(exc.code, exc.message)

    return ToolOutcome.failure('invalid_action', 'action must be "save", "update", "forget" or "search".')
