"""Turning the proactive assistant on: at first sign-in, or later from the Dashboard banner.

The person's assistant becomes their main assistant and checks in on a starter
brief, every four hours by default, with offers on. Check-ins are read-only;
the brief says not to send, buy or delete anything on its own.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger('cognitrix.log')

STARTER_BRIEF = {
    'keep_true': (
        "Nothing I owe anyone slips: replies, promises and deadlines are handled or flagged in time. "
        "My tasks and automations keep running."
    ),
    'sources': (
        "My connected apps (mail, calendar and the rest), my tasks and their runs, the Inbox, "
        "and what I said in recent chats."
    ),
    'tell_me': (
        "Mail or messages that need my reply, meetings coming up today, things I said I'd do that are "
        "coming due, tasks that are failing or stuck, and anything with a deadline in the next day."
    ),
    'not_yet': (
        "Don't send, post, buy, delete or change anything on your own. Prepare it and offer it instead."
    ),
}
STARTER_EVERY_MINUTES = 240


async def enable_defaults(agent: Any, owner_key: str, *, requested_by: str | None,
                          authority_kind: str, authority_id: str | None) -> bool:
    """Make ``agent`` the person's main assistant with check-ins on a starter
    brief. A brief the person already wrote is kept. Returns False when the
    agent's model can't use tools (check-ins need them)."""
    from cognitrix.tasks import standing

    if not getattr(agent.llm, 'supports_tool_use', True):
        return False
    current = standing.settings_of(agent)
    body: dict[str, Any] = {'enabled': True, 'primary': True, 'offers': True}
    if not standing.has_brief(current):
        body['brief'] = dict(STARTER_BRIEF)
        body['every_minutes'] = STARTER_EVERY_MINUTES
    settings = standing.clean_settings(body, current)
    await standing.sync_home_task(agent, settings, owner_key=owner_key, requested_by=requested_by,
                                  authority_kind=authority_kind, authority_id=authority_id)
    await standing.clear_other_primaries(agent, owner_key)
    await standing.save_settings(agent, settings)
    return True


async def main_assistant(owner_key: str) -> Any | None:
    """The person's main assistant: the one marked as such, else their first own agent."""
    from cognitrix.agents import access as agent_access
    from cognitrix.agents.starter_team import SPECIALISTS
    from cognitrix.tasks import standing

    agents = [a for a in await agent_access.visible_agents(owner_key)
              if str(getattr(a, 'owner_id', '') or '') == owner_key and not getattr(a, 'is_sub_agent', False)]
    for agent in agents:
        if standing.settings_of(agent)['primary']:
            return agent
    specialists = {s.name.lower() for s in SPECIALISTS}
    own = [a for a in agents if str(a.name).strip().lower() not in specialists]
    if not own:
        return None
    return min(own, key=lambda a: (str(getattr(a, 'created_at', '') or ''), str(a.id)))
