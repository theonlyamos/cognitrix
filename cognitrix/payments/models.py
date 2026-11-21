"""Payment settings, saved cards and payment requests."""
from __future__ import annotations

from odbms import Model
from pydantic import validator

PENDING, APPROVED, PAYING, DENIED, EXPIRED, PAID, FAILED, CANCELLED, UNCONFIRMED = (
    'pending', 'approved', 'paying', 'denied', 'expired', 'paid', 'failed', 'cancelled', 'unconfirmed')
OPEN = (PENDING, APPROVED)
"""Requests that still count against the limits until they end."""
COUNTED = (PENDING, APPROVED, PAYING, PAID, UNCONFIRMED)
"""Requests whose amounts count against the monthly limit."""


NO_AGENTS = 'none'
"""Stands in the list of agents for "nobody yet": no agent has this id."""


class PaymentSettings(Model):
    user_id: str
    enabled: bool = False
    """Off until the person turns it on: agents can't even ask before then."""
    currency: str = 'USD'
    per_payment_cents: int = 5_000
    """The most one payment may be."""
    monthly_cents: int = 20_000
    """The most all approved payments in a calendar month (UTC) may add up to."""
    agent_ids: list[str] = []
    """The agents that may ask. Empty means all of the person's agents (how
    older settings were saved); ``[NO_AGENTS]`` means none of them."""

    @validator('enabled', pre=True)
    def _bool(cls, value):
        return bool(value)

    @validator('currency', pre=True)
    def _currency(cls, value):
        return str(value or 'USD').upper()[:3]

    @validator('per_payment_cents', 'monthly_cents', pre=True)
    def _cents(cls, value):
        return max(0, int(value or 0))

    @validator('agent_ids', pre=True)
    def _ids(cls, value):
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value or '[]')
            except ValueError:
                value = []
        return [str(v) for v in (value or [])]


class PaymentCard(Model):
    user_id: str
    label: str = ''
    brand: str = ''
    last4: str = ''
    exp_month: int = 0
    exp_year: int = 0
    secret: str = ''
    """Sealed JSON: number, cvc, name, postal code. Never sent to the app or a model."""
    is_default: bool = False

    @validator('label', 'brand', 'last4', 'secret', pre=True)
    def _text(cls, value):
        return value or ''

    @validator('exp_month', 'exp_year', pre=True)
    def _int(cls, value):
        return int(value or 0)

    @validator('is_default', pre=True)
    def _flag(cls, value):
        return bool(value)


class PaymentRequest(Model):
    user_id: str
    agent_id: str = ''
    agent_name: str = ''
    session_id: str = ''
    task_id: str = ''
    merchant: str = ''
    """Who gets paid, as the agent put it ("Acme Books")."""
    site: str = ''
    """The checkout page's host: the card can only be filled in there."""
    checkout_url: str = ''
    amount_cents: int = 0
    currency: str = 'USD'
    purpose: str = ''
    """What it is for, in the agent's words."""
    items: str = ''
    status: str = PENDING
    card_id: str = ''
    decided_at: str = ''
    decided_by: str = ''
    approved_until: str = ''
    """An approval is used within minutes or it lapses."""
    expires_at: str = ''
    """A request nobody answered lapses."""
    paid_at: str = ''
    result: str = ''
    """What happened at the checkout, in plain words."""

    @validator('agent_id', 'agent_name', 'session_id', 'task_id', 'merchant', 'site', 'checkout_url', 'purpose',
               'items', 'card_id', 'decided_at', 'decided_by', 'approved_until', 'expires_at', 'paid_at', 'result',
               pre=True)
    def _text(cls, value):
        return value or ''

    @validator('amount_cents', pre=True)
    def _amount(cls, value):
        return int(value or 0)

    @validator('currency', pre=True)
    def _currency(cls, value):
        return str(value or 'USD').upper()[:3]


MODELS = (PaymentSettings, PaymentCard, PaymentRequest)
