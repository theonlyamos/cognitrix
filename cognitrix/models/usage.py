"""Token usage, added up per person, day, agent and model.

One row per (user, day, agent, provider, model) rather than one per model
call, so the table stays small and the usage page reads quickly. Costs are
not stored: prices change, so they are worked out when the page is read
(``cognitrix/usage/pricing.py``).
"""
from odbms import Model
from pydantic import field_validator


class UsageDay(Model):
    user_id: str
    day: str
    """UTC 'YYYY-MM-DD'."""
    agent_id: str = ''
    provider: str = ''
    model: str = ''
    calls: int = 0
    prompt_tokens: int = 0
    """Everything sent to the model, cached parts included."""
    completion_tokens: int = 0
    cached_tokens: int = 0
    """The part of prompt_tokens read from the provider's cache."""
    cache_write_tokens: int = 0
    """The part of prompt_tokens written to the provider's cache."""

    @field_validator('agent_id', 'provider', 'model', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('calls', 'prompt_tokens', 'completion_tokens', 'cached_tokens',
                     'cache_write_tokens', mode='before')
    @classmethod
    def _int_default(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
