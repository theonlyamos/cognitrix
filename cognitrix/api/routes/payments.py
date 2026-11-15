"""Agent payments: settings, cards, and the person's answer to each request.

    GET    /payments                       settings, cards (last 4 only), requests, this month's total
    PUT    /payments/settings              on/off, currency, limits, which agents may ask
    POST   /payments/cards                 add a card (the number never comes back)
    DELETE /payments/cards/{id}
    POST   /payments/requests/{id}/approve {amount_cents, card_id}: the total the person saw
    POST   /payments/requests/{id}/deny

Signed-in people only (``jwt_only``): an API key, a chat app or a push
notification button can't reach these, so only the person, in the app, can
approve a payment. See ``cognitrix/payments``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.payments import service
from cognitrix.payments.models import PaymentCard, PaymentRequest

payments_api = APIRouter(prefix='/payments', dependencies=[Depends(jwt_only)])


class SettingsBody(BaseModel):
    enabled: bool | None = None
    currency: str | None = Field(default=None, max_length=3)
    per_payment: float | None = Field(default=None, ge=0, le=1_000_000)
    monthly: float | None = Field(default=None, ge=0, le=10_000_000)
    agent_ids: list[str] | None = None


class CardBody(BaseModel):
    number: str = Field(max_length=30)
    exp_month: int
    exp_year: int
    cvc: str = Field(max_length=4)
    name: str = Field(default='', max_length=80)
    postal: str = Field(default='', max_length=12)
    label: str = Field(default='', max_length=60)


class ApproveBody(BaseModel):
    amount_cents: int
    card_id: str = ''


def _me(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


def _who(ctx: AuthContext) -> str:
    return str(getattr(ctx.user, 'email', '') or getattr(ctx.user, 'name', '') or _me(ctx))


def _raise(exc: service.PaymentError):
    raise HTTPException(status_code=exc.status, detail=str(exc)) from None


def _cents(value: float, currency: str) -> int:
    return round(value) if currency in service.ZERO_DECIMAL else round(value * 100)


async def _state(user_key: str) -> dict[str, Any]:
    await service.expire_old(user_key)
    settings = await service.get_settings(user_key)
    cards = await PaymentCard.find({'user_id': user_key}) or []
    rows = await PaymentRequest.find({'user_id': user_key}) or []
    rows.sort(key=lambda r: str(getattr(r, 'created_at', '') or ''), reverse=True)
    spent = await service.month_total(user_key, settings.currency)
    return {
        'settings': {
            'enabled': settings.enabled, 'currency': settings.currency,
            'per_payment_cents': settings.per_payment_cents, 'monthly_cents': settings.monthly_cents,
            'agent_ids': settings.agent_ids,
        },
        'currencies': list(service.CURRENCIES),
        'cards': [service.public_card(c) for c in cards],
        'requests': [service.public_request(r) for r in rows[:100]],
        'month_cents': spent,
        'approval_minutes': service.APPROVAL_MINUTES,
    }


@payments_api.get('')
async def get_payments(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return await _state(_me(ctx))


@payments_api.put('/settings')
async def put_settings(body: SettingsBody, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _me(ctx)
    current = await service.get_settings(user_key)
    currency = (body.currency or current.currency).upper()
    fields: dict[str, Any] = {}
    if body.enabled is not None:
        fields['enabled'] = body.enabled
    if body.currency is not None:
        fields['currency'] = currency
    if body.per_payment is not None:
        fields['per_payment_cents'] = _cents(body.per_payment, currency)
    if body.monthly is not None:
        fields['monthly_cents'] = _cents(body.monthly, currency)
    if body.agent_ids is not None:
        visible = {str(a.id) for a in await agent_access.visible_agents(user_key)}
        fields['agent_ids'] = [a for a in dict.fromkeys(body.agent_ids) if a in visible]
    try:
        await service.save_settings(user_key, **fields)
    except service.PaymentError as exc:
        _raise(exc)
    return await _state(user_key)


@payments_api.post('/cards')
async def add_card(body: CardBody, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    try:
        row = await service.add_card(_me(ctx), number=body.number, exp_month=body.exp_month,
                                     exp_year=body.exp_year, cvc=body.cvc, name=body.name, postal=body.postal,
                                     label=body.label)
    except service.PaymentError as exc:
        _raise(exc)
    return service.public_card(row)


@payments_api.delete('/cards/{card_id}')
async def delete_card(card_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, str]:
    try:
        await service.remove_card(_me(ctx), card_id)
    except service.PaymentError as exc:
        _raise(exc)
    return {'status': 'deleted'}


@payments_api.post('/requests/{request_id}/approve')
async def approve(request_id: str, body: ApproveBody, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    try:
        row = await service.approve(_me(ctx), request_id, amount_cents=body.amount_cents, card_id=body.card_id,
                                    decided_by=_who(ctx))
    except service.PaymentError as exc:
        _raise(exc)
    return service.public_request(row)


@payments_api.post('/requests/{request_id}/deny')
async def deny(request_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    try:
        row = await service.deny(_me(ctx), request_id, decided_by=_who(ctx))
    except service.PaymentError as exc:
        _raise(exc)
    return service.public_request(row)
