"""Payment tools (cognitrix/payments).

Request Payment asks the person to approve one payment: merchant, total and
the checkout page's site. It waits a while for their answer. Pay then makes
that exact payment on the agent's sandboxed computer: it checks the page,
types the card in and presses Pay itself, so the card number never reaches
the model. Cancel Payment calls a request off.

A turn gets these only when the person turned payments on and allows this
agent (``sessions/base.py``). Neither tool can approve anything: only the
person can, on the Payments page.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

PAYMENT_TOOL_CATEGORY = 'payments'
PAYMENT_TOOL_NAMES = ('request_payment', 'pay', 'cancel_payment')
WAIT_FOR_ANSWER_S = 600
POLL_S = 2.0


def _who(parent: Any) -> tuple[str, str, str, str, str]:
    from cognitrix.tools.utils import current_execution_context

    ctx = current_execution_context()
    agent_id = str(getattr(parent, 'id', None) or ctx.agent_id or '')
    return (str(ctx.user_id or ''), agent_id, str(getattr(parent, 'name', '') or ''), str(ctx.session_id or ''),
            str(ctx.task_id or ''))


def _guest() -> bool:
    try:
        from cognitrix.safety.permissions import guest_run

        return bool(guest_run.get())
    except Exception:
        return False


def _fail(exc: Any) -> ToolOutcome:
    return ToolOutcome.failure(getattr(exc, 'code', 'payment'), str(exc))


@tool(category=PAYMENT_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def request_payment(merchant: str, amount: str, purpose: str, checkout_url: str, currency: str = '',
                          items: str = '', *, parent: Any | None = None):
    """Ask the person to approve a payment before you pay anything. Use it at the checkout step, once the cart
    and the total are final. The person sees the merchant, total, site and purpose, and approves or declines in
    the app; this waits up to 10 minutes for the answer. After an approval, call Pay with the request id within
    30 minutes. Never type card details yourself.
    Args:
        merchant (str): Who gets paid, e.g. "Acme Books".
        amount (str): The final total to pay, as a number, e.g. "24.99".
        purpose (str): What it is for, in one or two sentences the person will read.
        checkout_url (str): The address of the checkout page where the card is entered.
        currency (str, optional): Three-letter currency code; the person's payments currency by default.
        items (str, optional): The items and their prices, one per line.
    """
    from cognitrix.payments import service
    from cognitrix.payments.models import PENDING

    user_id, agent_id, agent_name, session_id, task_id = _who(parent)
    if not user_id or _guest():
        return ToolOutcome.failure('no_owner', 'Only the person who owns this agent can be asked to pay.')
    try:
        row = await service.create_request(
            user_id, agent_id=agent_id, agent_name=agent_name, merchant=merchant, amount=amount,
            currency=currency, checkout_url=checkout_url, purpose=purpose, items=items, session_id=session_id,
            task_id=task_id)
    except service.PaymentError as exc:
        return _fail(exc)
    total = service.money(row.amount_cents, row.currency)
    deadline = time.monotonic() + WAIT_FOR_ANSWER_S
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_S)
        fresh = await service.PaymentRequest.get(row.id)
        if fresh is None or fresh.status != PENDING:
            row = fresh or row
            break
    status = row.status
    if status == 'approved':
        return (f'The person approved paying {total} to {row.merchant} on {row.site} (request {row.id}). '
                f'Call Pay with request_id "{row.id}" within {service.APPROVAL_MINUTES} minutes, with the checkout '
                'page open.')
    if status == 'denied':
        return ToolOutcome.failure('denied', f'The person declined paying {total} to {row.merchant}. Do not try '
                                   'to pay another way.', denied=True)
    if status == PENDING:
        return (f'Still waiting for the person to approve paying {total} to {row.merchant} (request {row.id}). '
                'They have been told. Do not pay any other way; check back later by calling Pay with this request '
                'id, which only works once they approve.')
    return ToolOutcome.failure(status, f'The payment request ended: {status}. {row.result}'.strip())


@tool(category=PAYMENT_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def pay(request_id: str, *, parent: Any | None = None):
    """Make a payment the person approved with Request Payment. Open the checkout page in the browser on your
    computer first, at the step where the card is entered. This checks that page is on the approved site and
    shows the approved total, types the card in and presses Pay. You never see the card. Works once per request.
    Args:
        request_id (str): The id from Request Payment.
    """
    from cognitrix.computer import current_actor
    from cognitrix.computer.sandbox.client import sandbox_mode
    from cognitrix.payments import checkout, service
    from cognitrix.tools.computer import _actor, _driver

    user_id, *_ = _who(parent)
    if not user_id or _guest():
        return ToolOutcome.failure('no_owner', 'Only the person who owns this agent can pay.')
    if not sandbox_mode():
        return ToolOutcome.failure('no_computer', "Payments are made on the agents' sandboxed computer, which is "
                                   'off. Ask the person to pay themselves.')
    try:
        row = await service.mine(user_id, request_id)
    except service.PaymentError as exc:
        return _fail(exc)
    if row.status == 'pending':
        return ToolOutcome.failure('not_approved', 'The person has not approved this payment yet. Wait, and do not '
                                   'pay any other way.')
    if row.status != 'approved':
        return ToolOutcome.failure('not_approved', f'This payment is {row.status}; it cannot be made.')
    actor = _actor(parent)
    viewer = f'payment:{row.id}'

    def run(fn, *args):
        token = current_actor.set(actor)
        try:
            return fn(_driver(actor), *args)
        finally:
            current_actor.reset(token)

    try:
        found = await asyncio.to_thread(run, checkout.find_checkout, row.site, row.amount_cents, row.currency,
                                        viewer)
    except checkout.CheckoutError as exc:
        return ToolOutcome.failure('checkout', f'{exc} The approval is still good until it lapses.')
    except Exception as exc:
        return ToolOutcome.failure('computer_use', f"Couldn't read the browser: {type(exc).__name__}. "
                                   'The approval is still good until it lapses.')
    try:
        row, card = await service.take_card(user_id, request_id)
    except service.PaymentError as exc:
        return _fail(exc)
    try:
        outcome, message = await asyncio.to_thread(run, checkout.fill_and_pay, found, card)
    except Exception:
        outcome, message = 'unconfirmed', ('Something went wrong while paying. Look at the page and tell the '
                                           'person; they should check whether the card was charged.')
    finally:
        del card
    row = await service.finish(row, outcome, message)
    total = service.money(row.amount_cents, row.currency)
    if outcome == 'paid':
        return f'Paid {total} to {row.merchant}. {message}'
    if outcome == 'failed':
        return ToolOutcome.failure('payment_failed', f'Not paid: {message} Ask the person before trying again; '
                                   'a new attempt needs a new approval.')
    return ToolOutcome.success(f'Not sure the payment of {total} went through: {message}')


@tool(category=PAYMENT_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def cancel_payment(request_id: str, reason: str = '', *, parent: Any | None = None):
    """Call off a payment request you made (the total changed, or it is no longer needed).
    Args:
        request_id (str): The id from Request Payment.
        reason (str, optional): Why, in a few words, for the person.
    """
    from cognitrix.payments import service

    user_id, *_ = _who(parent)
    try:
        row = await service.cancel(user_id, request_id, reason)
    except service.PaymentError as exc:
        return _fail(exc)
    return f'Payment request {row.id} is {row.status}.'
