"""Asking for a payment, the person's answer, the limits, and the card.

The flow:

1. An agent at a checkout calls Request Payment with the merchant, the total
   and the page it is on. ``create_request`` checks the person turned
   payments on, allows this agent, has a card, and that the total fits the
   per-payment and monthly limits; then the person gets an Inbox alert (and
   a push) that opens the Payments page. Nothing else is told.
2. The person approves or denies it there (``approve`` / ``deny``). Only the
   signed-in owner can, with the exact amount on the screen echoed back, and
   approving re-checks the limits. No other path in the app sets a request
   to approved.
3. Within ``APPROVAL_MINUTES`` the agent calls Pay, which takes the card once
   (``take_card``), fills it in on the approved site and presses Pay
   (``checkout.py``), and records what happened (``finish``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from cognitrix.payments.models import (
    APPROVED,
    CANCELLED,
    COUNTED,
    DENIED,
    EXPIRED,
    FAILED,
    PAID,
    PAYING,
    PENDING,
    UNCONFIRMED,
    PaymentCard,
    PaymentRequest,
    PaymentSettings,
)

logger = logging.getLogger('cognitrix.log')

APPROVAL_MINUTES = 30  # an approved payment must be made this soon
REQUEST_HOURS = 24  # an unanswered request lapses after this
MAX_PENDING = 5
CURRENCIES = ('USD', 'EUR', 'GBP', 'CAD', 'AUD', 'NZD', 'CHF', 'JPY', 'GHS', 'NGN', 'KES', 'ZAR', 'INR', 'SGD')
ZERO_DECIMAL = ('JPY',)


_card_lock = asyncio.Lock()


class PaymentError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = 'payment'):
        super().__init__(message)
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec='seconds')


def _parse(stamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def money(cents: int, currency: str) -> str:
    if currency in ZERO_DECIMAL:
        return f'{cents:,} {currency}'
    symbol = {'USD': '$', 'EUR': '€', 'GBP': '£'}.get(currency)
    amount = f'{cents / 100:,.2f}'
    return f'{symbol}{amount}' if symbol else f'{amount} {currency}'


def to_cents(amount: Any, currency: str) -> int:
    try:
        value = float(str(amount).replace(',', '').strip())
    except ValueError:
        raise PaymentError('Give the total as a number, like 24.99.') from None
    if not value or value <= 0 or value != value:
        raise PaymentError('The total must be more than zero.')
    return round(value) if currency in ZERO_DECIMAL else round(value * 100)


def site_of(url: str) -> str:
    """The host of a checkout page, lowercased, or '' if it isn't a web page."""
    text = (url or '').strip()
    if not re.match(r'^[a-z][a-z0-9+.\-]*://', text, re.I):
        text = 'https://' + text
    parts = urlsplit(text)
    if parts.scheme not in ('https', 'http'):
        return ''
    return (parts.hostname or '').lower().rstrip('.')


def same_site(host: str, site: str) -> bool:
    host, site = (host or '').lower().rstrip('.'), (site or '').lower()
    return bool(host and site) and (host == site or host.endswith('.' + site))


# ------------------------------------------------------------------ settings

async def get_settings(user_id: str) -> PaymentSettings:
    row = await PaymentSettings.find_one({'user_id': str(user_id)})
    return row or PaymentSettings(user_id=str(user_id))


async def save_settings(user_id: str, **fields: Any) -> PaymentSettings:
    row = await PaymentSettings.find_one({'user_id': str(user_id)})
    if 'currency' in fields and str(fields['currency']).upper() not in CURRENCIES:
        raise PaymentError('Pick one of the listed currencies.')
    if row is None:
        row = PaymentSettings(user_id=str(user_id), **fields)
        await row.save()
        return row
    if fields:
        await PaymentSettings.update_one({'id': row.id}, fields)
    return await PaymentSettings.get(row.id)


async def month_total(user_id: str, currency: str, when: datetime | None = None) -> int:
    """Cents asked for, approved or paid this calendar month (UTC)."""
    when = when or _now()
    start = f'{when.year:04d}-{when.month:02d}'
    total = 0
    for row in await PaymentRequest.find({'user_id': str(user_id)}) or []:
        made = str(getattr(row, 'created_at', '') or '')
        if row.status in COUNTED and row.currency == currency and made.startswith(start):
            total += int(row.amount_cents or 0)
    return total


# --------------------------------------------------------------------- cards

def card_brand(number: str) -> str:
    if number.startswith('4'):
        return 'Visa'
    if re.match(r'^(5[1-5]|2[2-7])', number):
        return 'Mastercard'
    if re.match(r'^3[47]', number):
        return 'American Express'
    if re.match(r'^(6011|65|64[4-9])', number):
        return 'Discover'
    return 'Card'


def luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return total % 10 == 0


async def add_card(user_id: str, *, number: str, exp_month: int, exp_year: int, cvc: str, name: str = '',
                   postal: str = '', label: str = '') -> PaymentCard:
    from cognitrix.common.secret_box import seal

    digits = re.sub(r'[\s-]', '', number or '')
    if not digits.isdigit() or not 12 <= len(digits) <= 19 or not luhn_ok(digits):
        raise PaymentError("That card number isn't valid.")
    cvc = (cvc or '').strip()
    if not cvc.isdigit() or len(cvc) not in (3, 4):
        raise PaymentError('The security code is 3 or 4 digits.')
    exp_year = int(exp_year or 0)
    if exp_year < 100:
        exp_year += 2000
    if not 1 <= int(exp_month or 0) <= 12:
        raise PaymentError('The expiry month is 1 to 12.')
    now = _now()
    if (exp_year, int(exp_month)) < (now.year, now.month):
        raise PaymentError('That card has expired.')
    mine = await PaymentCard.find({'user_id': str(user_id)}) or []
    if len(mine) >= 5:
        raise PaymentError('Remove a card first (5 at most).')
    brand = card_brand(digits)
    row = PaymentCard(
        user_id=str(user_id), brand=brand, last4=digits[-4:], exp_month=int(exp_month), exp_year=exp_year,
        label=(label or '').strip()[:60] or f'{brand} ending {digits[-4:]}', is_default=not mine,
        secret=seal(json.dumps({'number': digits, 'cvc': cvc, 'name': (name or '').strip()[:80],
                                'postal': (postal or '').strip()[:12]})),
    )
    await row.save()
    _forget_redactions(user_id)
    return row


async def remove_card(user_id: str, card_id: str) -> None:
    row = await PaymentCard.get(card_id)
    if row is None or row.user_id != str(user_id):
        raise PaymentError('Card not found.', 404)
    await PaymentCard.delete_many({'id': row.id})
    rest = await PaymentCard.find({'user_id': str(user_id)}) or []
    if row.is_default and rest:
        await PaymentCard.update_one({'id': rest[0].id}, {'is_default': True})
    _forget_redactions(user_id)


def public_card(row: PaymentCard) -> dict[str, Any]:
    return {'id': str(row.id), 'label': row.label, 'brand': row.brand, 'last4': row.last4,
            'exp_month': row.exp_month, 'exp_year': row.exp_year, 'is_default': row.is_default}


@dataclass(frozen=True)
class CardDetails:
    number: str
    cvc: str
    exp_month: int
    exp_year: int
    name: str
    postal: str
    last4: str

    def __repr__(self) -> str:  # never in a log or a traceback
        return f'CardDetails(ending {self.last4})'

    __str__ = __repr__


def _details(row: PaymentCard) -> CardDetails:
    from cognitrix.common.secret_box import unseal

    data = json.loads(unseal(row.secret) or '{}')
    return CardDetails(str(data.get('number', '')), str(data.get('cvc', '')), row.exp_month, row.exp_year,
                       str(data.get('name', '')), str(data.get('postal', '')), row.last4)


def number_variants(number: str) -> list[str]:
    """How a card number may show on a page: plain, in groups of 4, Amex style."""
    if len(number) < 12:
        return []
    out = {number, ' '.join(number[i:i + 4] for i in range(0, len(number), 4)),
           '-'.join(number[i:i + 4] for i in range(0, len(number), 4))}
    if len(number) == 15:
        out.add(f'{number[:4]} {number[4:10]} {number[10:]}')
    return sorted(out)


async def redaction_entries(user_id: str) -> dict[str, str]:
    """Card numbers to scrub out of every tool result (credentials.redaction_map_for)."""
    found: dict[str, str] = {}
    for row in await PaymentCard.find({'user_id': str(user_id)}) or []:
        try:
            details = _details(row)
        except Exception:
            continue
        for variant in number_variants(details.number):
            found[variant] = f'CARD ending {row.last4}'
    return found


def _forget_redactions(user_id: str) -> None:
    try:
        from cognitrix.credentials import forget_cached

        forget_cached(str(user_id))
    except Exception:
        pass


# ------------------------------------------------------------------ requests

async def _agent_allowed(settings: PaymentSettings, agent_id: str) -> bool:
    return not settings.agent_ids or str(agent_id) in settings.agent_ids


async def agent_may_ask(user_id: str, agent_id: str) -> bool:
    """Does this agent get the payment tools in this person's turns?"""
    settings = await PaymentSettings.find_one({'user_id': str(user_id)})
    if settings is None or not settings.enabled or not await _agent_allowed(settings, agent_id):
        return False
    return bool(await PaymentCard.find({'user_id': str(user_id)}))


async def expire_old(user_id: str) -> None:
    now = _now()
    for row in await PaymentRequest.find({'user_id': str(user_id)}) or []:
        if row.status == PENDING and (_parse(row.expires_at) or now) < now:
            await PaymentRequest.update_one({'id': row.id}, {'status': EXPIRED, 'result': 'Nobody answered in time.'})
        elif row.status == APPROVED and (_parse(row.approved_until) or now) < now:
            await PaymentRequest.update_one({'id': row.id}, {'status': EXPIRED,
                                                             'result': 'Approved, but not used in time.'})


async def create_request(user_id: str, *, agent_id: str, agent_name: str, merchant: str, amount: Any,
                         currency: str, checkout_url: str, purpose: str, items: str = '', session_id: str = '',
                         task_id: str = '') -> PaymentRequest:
    settings = await get_settings(user_id)
    if not settings.enabled:
        raise PaymentError('Payments are off. The person can turn them on under Settings, Payments.', 403,
                           'payments_off')
    if not await _agent_allowed(settings, agent_id):
        raise PaymentError("This agent isn't allowed to ask for payments (Settings, Payments).", 403,
                           'agent_not_allowed')
    cards = await PaymentCard.find({'user_id': str(user_id)}) or []
    if not cards:
        raise PaymentError('There is no card to pay with. The person can add one under Settings, Payments.', 409,
                           'no_card')
    currency = (currency or settings.currency).strip().upper()
    if currency != settings.currency:
        raise PaymentError(f'Payments are set up in {settings.currency}; this checkout is in {currency}.', 400,
                           'currency')
    cents = to_cents(amount, currency)
    merchant = re.sub(r'\s+', ' ', (merchant or '').strip())[:120]
    if not merchant:
        raise PaymentError('Say who gets paid (the merchant).')
    site = site_of(checkout_url)
    if not site or '.' not in site:
        raise PaymentError('Give the address of the checkout page the card goes into.')
    purpose = (purpose or '').strip()[:500]
    if not purpose:
        raise PaymentError('Say what the payment is for.')
    if cents > settings.per_payment_cents:
        raise PaymentError(f'{money(cents, currency)} is over the limit of {money(settings.per_payment_cents, currency)} '
                           'for one payment.', 403, 'over_limit')
    await expire_old(user_id)
    spent = await month_total(user_id, currency)
    if spent + cents > settings.monthly_cents:
        left = max(0, settings.monthly_cents - spent)
        raise PaymentError(f'That would go over this month\'s limit: {money(left, currency)} of '
                           f'{money(settings.monthly_cents, currency)} is left.', 403, 'over_limit')
    open_rows = [r for r in await PaymentRequest.find({'user_id': str(user_id), 'status': PENDING}) or []]
    if len(open_rows) >= MAX_PENDING:
        raise PaymentError('Too many payments are waiting for an answer already.', 429, 'busy')
    row = PaymentRequest(
        user_id=str(user_id), agent_id=str(agent_id or ''), agent_name=(agent_name or '')[:80],
        session_id=session_id or '', task_id=task_id or '', merchant=merchant, site=site,
        checkout_url=checkout_url.strip()[:1000], amount_cents=cents, currency=currency, purpose=purpose,
        items=(items or '').strip()[:1000], status=PENDING,
        expires_at=_stamp(_now() + timedelta(hours=REQUEST_HOURS)),
    )
    await row.save()
    await _tell(row)
    return row


async def _tell(row: PaymentRequest) -> None:
    try:
        from cognitrix.tasks.automation import create_notification

        who = row.agent_name or 'An agent'
        await create_notification(
            row.user_id, f'{who} wants to pay {money(row.amount_cents, row.currency)} to {row.merchant}',
            f'{row.purpose}\nOn {row.site}. Nothing is paid unless you approve it.',
            session_id=row.session_id or None, task_id=row.task_id or None, link=f'/payments?request={row.id}')
    except Exception:
        logger.exception('Could not send the payment alert')


async def mine(user_id: str, request_id: str) -> PaymentRequest:
    row = await PaymentRequest.get(request_id)
    if row is None or row.user_id != str(user_id):
        raise PaymentError('Payment not found.', 404, 'not_found')
    return row


async def approve(user_id: str, request_id: str, *, amount_cents: int, card_id: str = '',
                  decided_by: str = '') -> PaymentRequest:
    """The person says yes, in the app. ``amount_cents`` is the total they saw;
    it must match the request."""
    await expire_old(user_id)
    row = await mine(user_id, request_id)
    if row.status != PENDING:
        raise PaymentError(f'This payment is {row.status} already.', 409, 'not_pending')
    if int(amount_cents) != row.amount_cents:
        raise PaymentError('The amount you confirmed does not match the request. Reload and look again.', 409,
                           'amount_mismatch')
    settings = await get_settings(user_id)
    if not settings.enabled:
        raise PaymentError('Payments are off. Turn them on under Settings, Payments first.', 403, 'payments_off')
    if row.amount_cents > settings.per_payment_cents:
        raise PaymentError('This is over your limit for one payment. Raise the limit first.', 403, 'over_limit')
    others = await month_total(user_id, row.currency) - row.amount_cents
    if others + row.amount_cents > settings.monthly_cents:
        raise PaymentError("This would go over this month's limit. Raise the limit first.", 403, 'over_limit')
    cards = await PaymentCard.find({'user_id': str(user_id)}) or []
    card = next((c for c in cards if str(c.id) == card_id), None) if card_id else \
        next((c for c in cards if c.is_default), cards[0] if cards else None)
    if card is None:
        raise PaymentError('Pick a card to pay with.', 400, 'no_card')
    now = _now()
    await PaymentRequest.update_one({'id': row.id}, {
        'status': APPROVED, 'card_id': str(card.id), 'decided_at': _stamp(now), 'decided_by': decided_by,
        'approved_until': _stamp(now + timedelta(minutes=APPROVAL_MINUTES)),
    })
    _audit(user_id, 'payment.approve', row)
    return await PaymentRequest.get(row.id)


async def deny(user_id: str, request_id: str, decided_by: str = '') -> PaymentRequest:
    row = await mine(user_id, request_id)
    if row.status not in (PENDING, APPROVED):
        raise PaymentError(f'This payment is {row.status} already.', 409, 'not_pending')
    await PaymentRequest.update_one({'id': row.id}, {'status': DENIED, 'decided_at': _stamp(_now()),
                                                     'decided_by': decided_by, 'result': 'You said no.'})
    _audit(user_id, 'payment.deny', row)
    return await PaymentRequest.get(row.id)


async def cancel(user_id: str, request_id: str, reason: str = '') -> PaymentRequest:
    row = await mine(user_id, request_id)
    if row.status in (PENDING, APPROVED):
        await PaymentRequest.update_one({'id': row.id}, {'status': CANCELLED,
                                                         'result': (reason or 'The agent called it off.')[:300]})
    return await PaymentRequest.get(row.id)


async def take_card(user_id: str, request_id: str) -> tuple[PaymentRequest, CardDetails]:
    """The card for an approved, unused, unexpired payment: once. The request
    stops being usable here, whatever happens at the checkout."""
    async with _card_lock:
        await expire_old(user_id)
        row = await mine(user_id, request_id)
        if row.status == PENDING:
            raise PaymentError('The person has not approved this payment yet.', 409, 'not_approved')
        if row.status != APPROVED:
            raise PaymentError(f'This payment is {row.status}; it cannot be made.', 409, 'not_approved')
        card = await PaymentCard.get(row.card_id) if row.card_id else None
        if card is None or card.user_id != str(user_id):
            raise PaymentError('The card picked for this payment was removed.', 409, 'no_card')
        # Used from here on, before the card leaves this function, so two Pay
        # calls can't both fill it.
        await PaymentRequest.update_one({'id': row.id}, {'status': PAYING, 'result': 'Paying…'})
        fresh = await PaymentRequest.get(row.id)
    return fresh, _details(card)


async def finish(row: PaymentRequest, outcome: str, result: str) -> PaymentRequest:
    """``outcome``: 'paid', 'failed' (the card was not sent) or 'unconfirmed'
    (sent, but the page didn't show it went through: the person checks)."""
    status = {'paid': PAID, 'failed': FAILED}.get(outcome, UNCONFIRMED)
    paid = status == PAID
    fields: dict[str, Any] = {'status': status, 'result': result[:500]}
    if status != FAILED:
        fields['paid_at'] = _stamp(_now())
    await PaymentRequest.update_one({'id': row.id}, fields)
    _audit(row.user_id, f'payment.{status}', row)
    try:
        from cognitrix.tasks.automation import create_notification

        title = (f'Paid {money(row.amount_cents, row.currency)} to {row.merchant}' if paid else
                 f'Check the payment to {row.merchant}' if status == UNCONFIRMED else
                 f'Payment to {row.merchant} not made')
        await create_notification(row.user_id, title, result[:300], session_id=row.session_id or None,
                                  link=f'/payments?request={row.id}')
    except Exception:
        logger.debug('Could not send the payment result alert', exc_info=True)
    return await PaymentRequest.get(row.id)


def _audit(user_id: str, action: str, row: PaymentRequest) -> None:
    """Into the audit log when this server has one (PR #107); a log line otherwise."""
    logger.info('%s %s %s %s %s', action, row.id, row.merchant, row.amount_cents, row.currency)
    try:
        from cognitrix import audit  # type: ignore[attr-defined]

        record = getattr(audit, 'record_soon', None) or getattr(audit, 'record', None)
        if record is not None:
            out = record(str(user_id), action, target=str(row.id),
                         detail=f'{money(row.amount_cents, row.currency)} to {row.merchant} ({row.site})')
            if hasattr(out, '__await__'):
                import asyncio

                asyncio.ensure_future(out)
    except Exception:
        pass


def public_request(row: PaymentRequest) -> dict[str, Any]:
    return {
        'id': str(row.id), 'agent_id': row.agent_id, 'agent_name': row.agent_name, 'session_id': row.session_id,
        'task_id': row.task_id, 'merchant': row.merchant, 'site': row.site, 'checkout_url': row.checkout_url,
        'amount_cents': row.amount_cents, 'currency': row.currency, 'amount': money(row.amount_cents, row.currency),
        'purpose': row.purpose, 'items': row.items, 'status': row.status, 'card_id': row.card_id,
        'decided_at': row.decided_at, 'approved_until': row.approved_until, 'expires_at': row.expires_at,
        'paid_at': row.paid_at, 'result': row.result, 'created_at': str(getattr(row, 'created_at', '') or ''),
    }
