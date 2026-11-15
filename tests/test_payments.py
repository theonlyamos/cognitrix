"""Agent payments: only the person approves, limits hold, the card is used once
on the approved site, and the model never sees the number."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.common.security import AuthContext
from cognitrix.computer import Element

CARD = '4242424242424242'


def _ctx(user='u1'):
    return AuthContext(user=SimpleNamespace(id=user, role='member', email=f'{user}@x.test'), api_key=None)


@pytest.fixture
async def pay(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.models.credential import Credential
    from cognitrix.payments import service
    from cognitrix.payments.models import MODELS

    db_file = str(tmp_path / 'p.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (*MODELS, Credential):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()
    alerts = []

    async def note(user_id, title, message, **kw):
        alerts.append((user_id, title, kw.get('link')))

    import cognitrix.tasks.automation as automation
    monkeypatch.setattr(automation, 'create_notification', note)
    return SimpleNamespace(service=service, alerts=alerts)


async def _ready(service, user='u1', **settings):
    await service.save_settings(user, enabled=True, **settings)
    return await service.add_card(user, number=f'{CARD[:4]} {CARD[4:8]} {CARD[8:12]} {CARD[12:]}', exp_month=12,
                                  exp_year=2099, cvc='123', name='Ama Mensah', postal='00233')


def _ask(service, user='u1', amount='24.99', url='https://checkout.example.com/pay?x=1', **kw):
    return service.create_request(user, agent_id='a1', agent_name='Ada', merchant='Acme Books', amount=amount,
                                  currency='', checkout_url=url, purpose='The book you asked for', **kw)


async def test_nothing_happens_until_payments_are_on_and_a_card_is_saved(pay):
    s = pay.service
    with pytest.raises(s.PaymentError) as err:
        await _ask(s)
    assert err.value.code == 'payments_off'
    await s.save_settings('u1', enabled=True)
    with pytest.raises(s.PaymentError) as err:
        await _ask(s)
    assert err.value.code == 'no_card'
    assert not await s.agent_may_ask('u1', 'a1')
    with pytest.raises(s.PaymentError):
        await s.add_card('u1', number='4242424242424241', exp_month=1, exp_year=2099, cvc='123')  # bad checksum
    card = await _ready(s)
    assert card.last4 == '4242' and card.brand == 'Visa' and CARD not in card.secret
    assert await s.agent_may_ask('u1', 'a1')
    await s.save_settings('u1', agent_ids=['other'])
    assert not await s.agent_may_ask('u1', 'a1')


async def test_limits_and_the_persons_answer(pay):
    s = pay.service
    await _ready(s, per_payment_cents=5000, monthly_cents=6000)
    with pytest.raises(s.PaymentError) as err:
        await _ask(s, amount='60')
    assert err.value.code == 'over_limit'
    row = await _ask(s, amount='45.00')
    assert row.status == 'pending' and row.site == 'checkout.example.com' and row.amount_cents == 4500
    assert pay.alerts[-1][1] == 'Ada wants to pay $45.00 to Acme Books'
    assert pay.alerts[-1][2] == f'/payments?request={row.id}'
    with pytest.raises(s.PaymentError) as err:  # pending counts towards the month
        await _ask(s, amount='20')
    assert 'month' in str(err.value)

    # Pay is refused until the person approves; approving needs the amount they saw.
    with pytest.raises(s.PaymentError) as err:
        await s.take_card('u1', str(row.id))
    assert err.value.code == 'not_approved'
    with pytest.raises(s.PaymentError) as err:
        await s.approve('u1', str(row.id), amount_cents=4499)
    assert err.value.code == 'amount_mismatch'
    with pytest.raises(s.PaymentError) as err:
        await s.approve('u2', str(row.id), amount_cents=4500)
    assert err.value.status == 404
    done = await s.approve('u1', str(row.id), amount_cents=4500)
    assert done.status == 'approved' and done.card_id and done.approved_until

    taken, card = await s.take_card('u1', str(row.id))
    assert taken.status == 'paying' and card.number == CARD and CARD not in repr(card)
    with pytest.raises(s.PaymentError):  # once
        await s.take_card('u1', str(row.id))
    final = await s.finish(taken, 'paid', 'The checkout confirmed the order.')
    assert final.status == 'paid' and pay.alerts[-1][1] == 'Paid $45.00 to Acme Books'

    other = await _ask(s, amount='10')
    assert (await s.deny('u1', str(other.id))).status == 'denied'
    with pytest.raises(s.PaymentError):
        await s.approve('u1', str(other.id), amount_cents=1000)


async def test_card_numbers_are_scrubbed_from_tool_results(pay):
    from cognitrix.credentials import forget_cached, redact, redaction_map_for

    await _ready(pay.service)
    forget_cached('u1')
    found = await redaction_map_for('u1')
    text = redact('field value=4242 4242 4242 4242 and 4242424242424242', found)
    assert '4242 4242' not in text and CARD not in text and 'CARD ending 4242 hidden' in text


# --- the checkout page ------------------------------------------------------------


def _el(role, name='', value=None):
    return Element(role=role, name=name, value=value, rect=(10, 10, 50, 30))


class Shop:
    def __init__(self, host='checkout.example.com', total='$24.99', decline=False):
        self.host, self.total, self.decline = host, total, decline
        self.values = {}
        self.state = 'form'
        self.cleared = 0
        self.focus = None
        self.box = None

    def list_windows(self, query=None):
        return [SimpleNamespace(id=3, app='chromium', title='Checkout')]

    def _elements(self):
        bar = _el('entry', 'Address and search bar', f'{self.host}/pay')
        if self.state == 'done':
            return [bar, _el('heading', 'Thank you for your order'), _el('paragraph', '', 'Order number 1234')]
        items = [bar, _el('entry', 'Card number', self.values.get('Card number', '')),
                 _el('entry', 'Expiration date MM / YY', self.values.get('Expiration date MM / YY', '')),
                 _el('entry', 'Security code', self.values.get('Security code', '')),
                 _el('entry', 'Name on card', self.values.get('Name on card', '')),
                 _el('push button', f'Pay {self.total}')]
        if self.state == 'declined':
            items.append(_el('paragraph', '', 'Your card was declined.'))
        return items

    def look(self, window_id, query=None, limit=60, viewer=None):
        snap = SimpleNamespace(id='s', elements=self._elements(), origin=(0, 0), scale=1.0)
        return None, snap, [], ''

    def type_text(self, window_id, text, x=None, y=None, viewer=None, use_screen=False):
        # the field is found by order of typing in this fake: remember the last field looked for
        name = self.next_field
        self.values[name] = text

    def act_on_element(self, window_id, index, action='click', snapshot_id=None, viewer=None):
        name = self._elements()[index].name
        assert name.startswith('Pay')
        self.state = 'declined' if self.decline else 'done'

    def click_point(self, *a, **kw):
        pass

    def press_key(self, window_id, keys, use_screen=False):
        if keys == 'backspace':
            self.cleared += 1
            self.values[self.next_field] = ''


@pytest.fixture
def shop(monkeypatch):
    from cognitrix.payments import checkout

    real_field = checkout.Page.field
    holder = {}

    def field(self, labels, exclude=()):
        index = real_field(self, labels, exclude)
        if index is not None and holder.get('shop'):
            holder['shop'].next_field = self.elements[index].name
        return index

    monkeypatch.setattr(checkout.Page, 'field', field)
    monkeypatch.setattr(checkout.time, 'sleep', lambda s: None)

    def make(**kw):
        holder['shop'] = Shop(**kw)
        holder['shop'].next_field = ''
        return holder['shop']
    return make


def test_the_card_goes_in_only_on_the_approved_site_with_the_approved_total(shop):
    from cognitrix.payments import checkout
    from cognitrix.payments.service import CardDetails

    card = CardDetails(CARD, '123', 12, 2099, 'Ama Mensah', '', '4242')
    with pytest.raises(checkout.CheckoutError, match='evil.example.net'):
        checkout.find_checkout(shop(host='evil.example.net'), 'checkout.example.com', 2499, 'USD', 'v')
    with pytest.raises(checkout.CheckoutError, match='total'):
        checkout.find_checkout(shop(total='$99.00'), 'checkout.example.com', 2499, 'USD', 'v')

    page = shop()
    found = checkout.find_checkout(page, 'example.com', 2499, 'USD', 'v')
    outcome, message = checkout.fill_and_pay(page, found, card, 'v', wait_s=1, step_s=0)
    assert outcome == 'paid' and CARD not in message
    assert page.values['Card number'] == CARD and page.values['Expiration date MM / YY'] == '12/99'
    assert page.values['Security code'] == '123' and page.values['Name on card'] == 'Ama Mensah'

    declined = shop(decline=True)
    found = checkout.find_checkout(declined, 'checkout.example.com', 2499, 'USD', 'v')
    outcome, message = checkout.fill_and_pay(declined, found, card, 'v', wait_s=1, step_s=0)
    assert outcome == 'failed' and 'declined' in message and declined.cleared >= 1


# --- the app ----------------------------------------------------------------------


async def test_only_the_signed_in_owner_answers_and_settings_are_their_own(pay, monkeypatch):
    from cognitrix.agents import access
    from cognitrix.api.routes import payments as routes

    async def visible(_user):
        return [SimpleNamespace(id='a1'), SimpleNamespace(id='a2')]

    monkeypatch.setattr(access, 'visible_agents', visible)
    state = await routes.put_settings(routes.SettingsBody(enabled=True, per_payment=30, monthly=100,
                                                          agent_ids=['a1', 'nope']), _ctx('u1'))
    assert state['settings'] == {'enabled': True, 'currency': 'USD', 'per_payment_cents': 3000,
                                 'monthly_cents': 10000, 'agent_ids': ['a1']}
    card = await routes.add_card(routes.CardBody(number=CARD, exp_month=1, exp_year=99, cvc='321'), _ctx('u1'))
    assert set(card) == {'id', 'label', 'brand', 'last4', 'exp_month', 'exp_year', 'is_default'}
    row = await _ask(pay.service, amount='12.50')
    listed = await routes.get_payments(_ctx('u2'))
    assert listed['requests'] == [] and listed['cards'] == []
    with pytest.raises(HTTPException) as err:
        await routes.approve(str(row.id), routes.ApproveBody(amount_cents=1250), _ctx('u2'))
    assert err.value.status_code == 404
    out = await routes.approve(str(row.id), routes.ApproveBody(amount_cents=1250), _ctx('u1'))
    assert out['status'] == 'approved' and out['amount'] == '$12.50'
    assert (await routes.get_payments(_ctx('u1')))['month_cents'] == 1250


async def test_request_payment_waits_for_the_answer(pay, monkeypatch):
    import asyncio

    from cognitrix.tools import payments as tools
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    await _ready(pay.service)
    monkeypatch.setattr(tools, 'POLL_S', 0.01)
    monkeypatch.setattr(tools, 'WAIT_FOR_ANSWER_S', 2)
    token = set_execution_context(ToolExecutionContext(user_id='u1', agent_id='a1', session_id='s1'))
    try:
        async def answer():
            for _ in range(100):
                rows = await pay.service.PaymentRequest.find({'user_id': 'u1'})
                if rows:
                    await pay.service.approve('u1', str(rows[0].id), amount_cents=1999)
                    return
                await asyncio.sleep(0.01)

        task = asyncio.create_task(answer())
        out = await tools.request_payment.run(merchant='Acme', amount='19.99', purpose='Book',
                                              checkout_url='https://shop.example.com/checkout',
                                              parent=SimpleNamespace(id='a1', name='Ada'))
        await task
    finally:
        reset_execution_context(token)
    assert 'approved paying $19.99 to Acme' in str(out.content)


def test_payment_tools_carry_their_own_approval():
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel

    d = DestructiveOpDetector()
    for name in ('Request Payment', 'Pay', 'Cancel Payment'):
        assert d.analyze(name, {}).risk_level == RiskLevel.LOW
