"""Paying at a checkout page on the agent's computer.

Pay (``tools/payments.py``) runs this for one approved payment:

1. ``find_checkout``: a browser window whose address bar shows the approved
   site, with a card form on it and the approved total somewhere on the page.
   If any of that is missing nothing is used up, and the agent is told what
   to fix.
2. Only then is the card taken (``service.take_card``, once), typed into the
   form's fields and Pay pressed (``fill_and_pay``), all inside this one
   call, so the model never reads the number. Every tool result is also
   scrubbed of saved card numbers (``credentials.redaction_map_for``).
3. The page is read until it says the payment went through or was declined.
   A declined card is cleared from the form before the agent looks again.

All driver calls block: run these in a thread.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from cognitrix.payments.service import CardDetails, same_site, site_of

logger = logging.getLogger('cognitrix.log')

ADDRESS_BAR = ('address and search bar', 'address bar', 'search or enter address', 'search with google or enter address',
               'location')
TEXT_ROLES = ('entry', 'text', 'password text', 'combo box', 'textbox', 'spin button')
BUTTON_ROLES = ('push button', 'button', 'link', 'toggle button')
NUMBER = ('card number', 'credit card number', 'debit card number', 'credit or debit card number', 'card no')
EXPIRY = ('expiration date', 'expiry date', 'expiration', 'expiry', 'mm / yy', 'mm/yy', 'mm / yyyy', 'exp date',
          'valid thru')
MONTH = ('expiration month', 'expiry month', 'exp month')
YEAR = ('expiration year', 'expiry year', 'exp year')
CVC = ('security code', 'cvc', 'cvv', 'card code', 'csc', 'card verification')
NAME = ('name on card', 'cardholder name', 'card holder name', 'cardholder', 'name on the card')
POSTAL = ('zip', 'postal code', 'postcode', 'post code')
PAY = ('pay', 'place order', 'place your order', 'complete order', 'complete purchase', 'complete payment',
       'buy now', 'confirm and pay', 'confirm payment', 'submit payment', 'submit order', 'purchase', 'subscribe',
       'make payment')
PAID_WORDS = ('thank you for your order', 'thanks for your order', 'order confirmed', 'payment successful',
              'payment complete', 'payment received', 'your order has been placed', 'order number', 'order #',
              'thank you for your purchase', 'thanks for your purchase', 'receipt', 'payment succeeded',
              'purchase complete', 'order complete')
DECLINED_WORDS = ('declined', 'was not successful', 'could not be processed', "couldn't be processed",
                  'insufficient funds', 'invalid card', 'card number is invalid', 'incorrect', 'try another card',
                  'payment failed', 'is invalid', 'expired card', 'has expired', 'unable to process')
CHECK_WORDS = ('verify', 'authenticate', 'authentication', '3d secure', 'one-time code', 'one time passcode',
               'confirm in your banking app', 'approve in your bank')
WAIT_S = 45
STEP_S = 2.0


def _norm(text: Any) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()


@dataclass
class Page:
    window_id: int
    snapshot_id: str
    elements: list[Any]
    origin: tuple[int, int]
    scale: float

    def text(self) -> str:
        return ' | '.join(b for el in self.elements for b in (_norm(el.name), _norm(el.value)[:300]) if b)

    def address(self) -> str:
        for el in self.elements:
            if el.role.lower() in TEXT_ROLES and any(label in _norm(el.name) for label in ADDRESS_BAR) and el.value:
                return str(el.value).strip()
        return ''

    def field(self, labels: tuple[str, ...], exclude: tuple[str, ...] = ()) -> int | None:
        for label in labels:
            for index, el in enumerate(self.elements):
                name = _norm(el.name)
                if (el.enabled and el.role.lower() in TEXT_ROLES and label in name
                        and not any(x in name for x in exclude + ADDRESS_BAR)):
                    return index
        return None

    def button(self, labels: tuple[str, ...]) -> int | None:
        for label in labels:
            for index, el in enumerate(self.elements):
                name = _norm(el.name)
                if el.enabled and el.role.lower() in BUTTON_ROLES and (
                        name == label or name.startswith(label + ' ') or name.startswith(label + ',')):
                    return index
        return None

    def point(self, index: int) -> tuple[float, float]:
        r = self.elements[index].rect
        return (((r[0] + r[2]) / 2 - self.origin[0]) / self.scale, ((r[1] + r[3]) / 2 - self.origin[1]) / self.scale)


class CheckoutError(Exception):
    """Nothing was paid, and the card was not used."""


def amount_texts(cents: int, currency: str) -> list[str]:
    """How the approved total may be written on a page: 1,234.50 / 1234.50 / 1.234,50 / 1234,50."""
    if currency == 'JPY':
        return [f'{cents:,}', str(cents)]
    whole, part = divmod(cents, 100)
    out = [f'{whole:,}.{part:02d}', f'{whole}.{part:02d}', f'{whole:,}.{part:02d}'.replace(',', '.')[:-3]
           + f',{part:02d}', f'{whole},{part:02d}']
    if part == 0:
        out += [f'{whole:,}', str(whole)]
    return list(dict.fromkeys(out))


def _look(driver: Any, window_id: int, viewer: str) -> Page:
    _image, snap, _shown, _note = driver.look(window_id, None, 1, viewer=viewer)
    return Page(window_id, snap.id, list(snap.elements), tuple(snap.origin), float(snap.scale or 1.0))


@dataclass
class Checkout:
    page: Page
    host: str


def find_checkout(driver: Any, site: str, cents: int, currency: str, viewer: str) -> Checkout:
    """The open checkout on ``site``, checked before the card is touched."""
    windows = [w for w in driver.list_windows() if any(b in (w.app or '').lower() for b in ('chrom', 'firefox'))]
    if not windows:
        raise CheckoutError('No browser window is open. Open the checkout page first.')
    seen_hosts = []
    for win in windows:
        try:
            page = _look(driver, int(win.id), viewer)
        except Exception as exc:
            logger.info('Pay: could not read window %s: %s', win.id, exc)
            continue
        host = site_of(page.address())
        if host:
            seen_hosts.append(host)
        if not same_site(host, site):
            continue
        if page.field(NUMBER) is None:
            raise CheckoutError(f'The page on {host} has no card number field I can read. Go to the step where '
                                'the card is entered, then call Pay again.')
        if page.field(EXPIRY, exclude=('month', 'year')) is None and (page.field(MONTH) is None
                                                                       or page.field(YEAR) is None):
            raise CheckoutError(f'The card form on {host} has no expiry field I can fill. Ask the person to take '
                                'over and pay themselves.')
        body = page.text()
        if not any(amount in body for amount in amount_texts(cents, currency)):
            raise CheckoutError(f'The approved total does not show on the page on {host}. If the total changed, '
                                'cancel this payment and ask again with the new total.')
        return Checkout(page, host)
    where = ', '.join(sorted(set(seen_hosts))) or 'no readable address'
    raise CheckoutError(f'The approved site is {site}, but the open browser windows show {where}. Open the '
                        f'checkout on {site} and call Pay again.')


def _type(driver: Any, page: Page, index: int, text: str, viewer: str) -> None:
    x, y = page.point(index)
    driver.type_text(page.window_id, text, x, y, viewer=viewer, use_screen=True)


def _clear(driver: Any, page: Page, index: int, viewer: str) -> None:
    try:
        x, y = page.point(index)
        driver.click_point(page.window_id, x, y, viewer=viewer, use_screen=True)
        driver.press_key(page.window_id, 'ctrl+a', use_screen=True)
        driver.press_key(page.window_id, 'backspace', use_screen=True)
    except Exception:
        logger.info('Pay: could not clear a card field', exc_info=False)


def fill_and_pay(driver: Any, checkout: Checkout, card: CardDetails, viewer: str,
                 wait_s: float = WAIT_S, step_s: float = STEP_S) -> tuple[str, str]:
    """Type the card in and press Pay. Returns (outcome, what happened) with
    outcome 'paid', 'failed' (nothing was sent) or 'unconfirmed'. Never
    returns or logs the card's details."""
    page = checkout.page
    win = page.window_id
    fields: list[tuple[tuple[str, ...], tuple[str, ...], str]] = [
        (NUMBER, (), card.number),
        (CVC, (), card.cvc),
    ]
    if page.field(EXPIRY, exclude=('month', 'year')) is not None:
        fields.append((EXPIRY, ('month', 'year'), f'{card.exp_month:02d}/{card.exp_year % 100:02d}'))
    else:
        fields.append((MONTH, (), f'{card.exp_month:02d}'))
        fields.append((YEAR, (), str(card.exp_year)))
    if card.name:
        fields.append((NAME, (), card.name))
    if card.postal:
        fields.append((POSTAL, (), card.postal))
    try:
        for labels, exclude, value in fields:
            page = _look(driver, win, viewer)  # numbers change as fields fill
            index = page.field(labels, exclude=exclude)
            if index is None:
                if labels in (NAME, POSTAL):
                    continue  # the form doesn't ask for it
                return 'failed', 'The card form changed while it was being filled. Nothing was paid.'
            if _norm(page.elements[index].value) and labels in (NAME, POSTAL):
                continue  # already filled in by the agent or the site
            _type(driver, page, index, value, viewer)
            time.sleep(0.3)
        page = _look(driver, win, viewer)
        pay = page.button(PAY)
        if pay is None:
            _wipe(driver, win, viewer)
            return 'failed', 'There is no Pay or Place order button I can press, so nothing was paid.'
        driver.act_on_element(win, pay, 'click', snapshot_id=page.snapshot_id, viewer=viewer)
    except Exception as exc:
        logger.warning('Pay: filling the form failed (%s)', type(exc).__name__)
        _wipe(driver, win, viewer)
        return 'failed', 'Filling in the card failed, so nothing was paid. Ask the person to take over.'

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(step_s)
        try:
            page = _look(driver, win, viewer)
        except Exception:
            continue
        body = page.text()
        declined = next((w for w in DECLINED_WORDS if w in body), '')
        if declined and page.field(NUMBER) is not None:
            _wipe(driver, win, viewer)
            return 'failed', f'The checkout said "{declined}". The card was cleared from the form.'
        if any(w in body for w in PAID_WORDS) and page.field(NUMBER) is None:
            return 'paid', 'The checkout confirmed the order.'
        if any(w in body for w in CHECK_WORDS):
            return 'unconfirmed', ('The bank wants to confirm this payment. Ask the person to approve it in their '
                                   'banking app, or to take over the computer for the code.')
    return 'unconfirmed', ("Pay was pressed, but the page didn't clearly say it went through. Look at the page and "
                           'tell the person.')


def _wipe(driver: Any, win: int, viewer: str) -> None:
    """Take the card number back out of the form, so a later look can't read it."""
    try:
        page = _look(driver, win, viewer)
    except Exception:
        return
    for labels in (NUMBER, CVC):
        index = page.field(labels)
        if index is not None:
            _clear(driver, page, index, viewer)
