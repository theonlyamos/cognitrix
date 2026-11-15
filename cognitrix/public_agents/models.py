"""A public link to an agent: anyone with the link (or a website that embeds
it) can chat with the agent, without an account.

Persistence rule (as elsewhere): instance ``save()`` once at creation,
``update_one`` after.
"""
from __future__ import annotations

from typing import Any

from odbms import Model
from pydantic import Field, field_validator

DEFAULT_DAILY_LIMIT = 200
DEFAULT_VISITOR_HOURLY_LIMIT = 20
MAX_DAILY_LIMIT = 10_000
MAX_VISITOR_HOURLY_LIMIT = 500
MAX_SITES = 20
TITLE_MAX = 80
GREETING_MAX = 500
INSTRUCTIONS_MAX = 4000


def _text(value: Any) -> str:
    return '' if value is None else str(value)


class PublicAgentLink(Model):
    agent_id: str
    """One link per agent."""
    created_by: str = ''
    """Principal key of the owner who turned it on. Usage is always counted
    for the agent's current owner, read when a visitor writes."""
    token: str
    """The secret part of the link. Rotating it turns the old link off."""
    enabled: bool = True
    title: str = ''
    """Shown at the top of the chat; the agent's name when empty."""
    greeting: str = ''
    """First message visitors see, before they write anything."""
    instructions: str = ''
    """Extra instructions for the agent when it answers visitors."""
    web_search: bool = False
    """Let it use its web search tools for visitors (only if the agent has them)."""
    sites: list = Field(default_factory=list)
    """Websites allowed to embed the chat (origins). Empty: any website."""
    daily_limit: int = DEFAULT_DAILY_LIMIT
    """Visitor messages per day, all visitors together."""
    visitor_hourly_limit: int = DEFAULT_VISITOR_HOURLY_LIMIT
    """Messages per hour from one visitor."""
    day: str = ''
    """UTC day the count below is for."""
    day_count: int = 0
    created: str = ''
    updated: str = ''

    @field_validator('created_by', 'title', 'greeting', 'instructions', 'day', 'created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)

    @field_validator('enabled', 'web_search', mode='before')
    @classmethod
    def _flag(cls, value):
        return bool(value)

    @field_validator('sites', mode='before')
    @classmethod
    def _sites(cls, value):
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value)
            except ValueError:
                return []
        return [str(v) for v in value] if isinstance(value, list) else []

    @field_validator('daily_limit', 'visitor_hourly_limit', 'day_count', mode='before')
    @classmethod
    def _int(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


MODELS = (PublicAgentLink,)
