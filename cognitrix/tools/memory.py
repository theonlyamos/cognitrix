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
    'project': 'project', 'this_project': 'project', 'project_only': 'project',
}


def _proposal_reason(settings: Any) -> str:
    """Why a save or update has to wait for the person, or '' when it doesn't."""
    from cognitrix.memory import guard

    if settings.auto_save == 'ask':
        return 'This person approves each new memory themselves.'
    reads = guard.untrusted_reads()
    if reads:
        return (f'You read outside content this turn ({", ".join(reads[:3])}), so memory changes '
                'need the person\'s OK.')
    return ''


def _proposed(row: Any, reason: str) -> ToolOutcome:
    where = row.scope if row.scope in ('agent', 'project') else 'global'
    return ToolOutcome.success(
        f'Memory proposed (id {row.id}, {where}): {row.content}\n'
        f'{reason} It is waiting for their OK in the chat; it is not saved until they approve it.')


def _line(row: Any) -> str:
    where = {'agent': 'this agent only', 'project': 'this project only'}.get(row.scope, 'global')
    until = f', until {row.valid_until}' if getattr(row, 'valid_until', '') else ''
    return f'- {row.content} (id {row.id}, {where}{until})'


_KIND_WORDS = {
    'about': 'about', 'me': 'about', 'user': 'about', 'fact': 'about', 'profile': 'about',
    'preference': 'preference', 'preferences': 'preference', 'pref': 'preference', 'like': 'preference',
    'person': 'person', 'people': 'person', 'contact': 'person',
    'project': 'project', 'work': 'project',
    'howto': 'howto', 'how_to': 'howto', 'how-to': 'howto', 'instruction': 'howto', 'procedure': 'howto',
    'other': 'other', 'commitment': 'other', 'reminder': 'other', 'deadline': 'other',
}


def _kind(value: str) -> str:
    """Read a model's word for the kind generously; unknown words are dropped."""
    text = str(value or '').strip().lower().replace(' ', '_')
    return _KIND_WORDS.get(text, '')


@tool(category=MEMORY_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def memory(action: str, content: str = '', memory_id: str = '', scope: str = '',
                 query: str = '', kind: str = '', valid_until: str = '', *, parent=None) -> ToolOutcome:
    """Your long-term memory about the person you're working for, kept between chats.
    What you already remember is listed in your instructions under "What you remember about the user".

    action:
    - "save": remember one short fact (memory_id is not needed; the app assigns one) (content, at most 300 characters). Save lasting things the person
      tells you about themselves, their work, their preferences and decisions on ongoing projects, or
      anything they ask you to remember. Don't save one-off task details, things you can look up again,
      passwords or keys, or private details about other people.
      scope: "global" (every agent of this person sees it), "agent" (only you) or "project" (only chats in this
        project; the default inside a project). Leave it empty for your default.
      kind (optional): "about" (the person), "preference", "person" (someone they know), "project",
      "howto" (how they like something done) or "other".
      valid_until (optional, YYYY-MM-DD): for facts that stop being true, like "on leave until the 20th"
      or "the launch is on Friday". After that day it is no longer shown to you.
      Also save commitments and things being waited on ("I'll send Ama the deck on Friday",
      "waiting for Sam's numbers") with a valid_until on the due date: the assistant follows up on them.
    - "update": replace a memory that changed (memory_id and the new content).
    - "forget": delete a memory (memory_id). Do it when the person asks or a memory turns out wrong.
    - "search": find saved memories that aren't in your instructions (query).
    Search matches by meaning, so "my partner" finds "Married to Ama".
    The person sees a note in the chat each time you save, update or forget something. Sometimes a
    save or update is only proposed: the person approves it in the chat (when they asked for that, or
    after you read web pages, files or app content in this turn). Don't save it again; tell them it
    is waiting for their OK."""
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
            from cognitrix.projects.context import get as _turn_project
            in_project = _turn_project() is not None
            if chosen == 'project' and not in_project:
                chosen = ''
            if not chosen and in_project:
                chosen = 'project'  # what's learned in a project stays with it
            if not chosen:
                chosen = 'global' if await saved.agent_saves_global(parent) else 'agent'
            if chosen == 'global' and not saved.agent_reads_global(parent):
                chosen = 'agent'
            if chosen == 'agent' and not agent_id:
                chosen = 'global'
            reason = _proposal_reason(settings)
            row, created = await saved.add(
                user_id, content, scope=chosen, agent_id=agent_id,
                source='agent', session_id=str(ctx.session_id or ''), pending=bool(reason),
                kind=_kind(kind), valid_until=valid_until)
            if not created:
                if row.pending:
                    return ToolOutcome.success(
                        f'Already proposed (id {row.id}): {row.content}. It is waiting for the person\'s OK.')
                return ToolOutcome.success(
                    f'Already remembered (id {row.id}): {row.content}. '
                    'Use action "update" if it needs to change.')
            if reason:
                return _proposed(row, reason)
            return ToolOutcome.success(f'Memory saved (id {row.id}, {row.scope}): {row.content}')

        if action in ('update', 'forget'):
            row = await saved.get_memory(user_id, memory_id)
            if row is None or not saved._visible(row, agent_id, saved.agent_reads_global(parent)):
                return ToolOutcome.failure(
                    'not_found', f'No memory with id {memory_id!r}. Use action "search" to find its id.')
            if action == 'forget':
                await saved.delete(row)
                return ToolOutcome.success(f'Memory forgotten (id {row.id}): {row.content}')
            reason = _proposal_reason(settings)
            if reason:
                proposal, _created = await saved.add(
                    user_id, content or row.content, scope=row.scope, agent_id=row.agent_id,
                    project_id=row.project_id, source='agent',
                    session_id=str(ctx.session_id or ''), pending=True, replaces=str(row.id),
                    kind=_kind(kind) or row.kind, valid_until=valid_until or row.valid_until)
                if not proposal.pending:
                    return ToolOutcome.success(f'Already remembered (id {proposal.id}): {proposal.content}.')
                return _proposed(proposal, reason)
            extra: dict[str, Any] = {}
            if kind:
                extra['kind'] = _kind(kind)
            if valid_until:
                extra['valid_until'] = valid_until
            if not str(content or '').strip() and not extra:
                return ToolOutcome.failure('content_required', 'Give the new content, kind or valid_until.')
            row = await saved.update(row, content=content or None, **extra)
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
