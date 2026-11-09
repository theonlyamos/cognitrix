"""People management, invites and password reset by email."""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_session_ownership_routes import _initialize_routes_db


@pytest.fixture
async def people(tmp_path, monkeypatch):
    from cognitrix.agents import Agent
    from cognitrix.common.utils import Utils
    from cognitrix.models import User
    from cognitrix.models.invite import Invite
    from cognitrix.models.usage import UsageDay

    for name in ('COGNITRIX_ADMIN_EMAILS', 'COGNITRIX_ALLOW_SIGNUP', 'COGNITRIX_SMTP_HOST'):
        monkeypatch.delenv(name, raising=False)
    await _initialize_routes_db(tmp_path)
    for model in (User, Invite, UsageDay, Agent):
        await model._create_table_async()
    from cognitrix.usage import pricing

    async def no_prices():
        return {}
    monkeypatch.setattr(pricing, 'load', no_prices)

    async def make(name, email, **extra):
        await User(name=name, email=email, password=Utils.hash_password('old-password'), **extra).save()
        time.sleep(0.01)
        return await User.find_one({'email': email})

    return SimpleNamespace(ada=await make('Ada', 'ada@example.com'), bob=await make('Bob', 'bob@example.com'), make=make)


def _ctx(user):
    from cognitrix.common.security import AuthContext
    return AuthContext(user=user)


def _request(invite=None):
    return SimpleNamespace(query_params={'invite': invite} if invite else {}, base_url='http://testserver/')


async def test_only_admins_manage_people_and_the_last_admin_stays(people):
    from cognitrix.api.routes import admin
    from cognitrix.common.security import authenticate
    from cognitrix.models import User

    with pytest.raises(HTTPException) as exc_info:
        await admin.admin_only(_ctx(people.bob))
    assert exc_info.value.status_code == 403
    ctx = await admin.admin_only(_ctx(people.ada))

    listed = (await admin.list_users(ctx))['users']
    assert [(u['email'], u['role'], u['you']) for u in listed] == [
        ('ada@example.com', 'admin', True), ('bob@example.com', 'member', False)]

    with pytest.raises(HTTPException):  # the only admin can't step down
        await admin.update_user(str(people.ada.id), admin.UserUpdate(role='member'), ctx)

    row = await admin.update_user(str(people.bob.id), admin.UserUpdate(disabled=True), ctx)
    assert row['disabled'] and not await authenticate('bob@example.com', 'old-password')
    assert (await User.get(people.bob.id)).tokens_valid_after

    row = await admin.update_user(
        str(people.bob.id), admin.UserUpdate(disabled=False, role='admin', new_password='brand-new-1'), ctx)
    assert row['role'] == 'admin' and await authenticate('bob@example.com', 'brand-new-1')


async def test_invite_lets_one_person_in_while_signup_is_closed(people):
    from cognitrix.api.routes import admin, auth
    from cognitrix.models import User

    newcomer = lambda: User(name='Cy', email='cy@example.com', password='long-enough-1', role='admin')  # noqa: E731
    with pytest.raises(HTTPException) as exc_info:
        await auth.signup(newcomer(), _request())
    assert exc_info.value.status_code == 403

    invite = await admin.create_invite(admin.InviteBody(email='cy@example.com'), _ctx(people.ada))
    assert (await auth.auth_options(invite['token']))['signup_open'] is True
    with pytest.raises(HTTPException):  # meant for someone else
        await auth.signup(User(name='Mal', email='mal@example.com', password='long-enough-1'), _request(invite['token']))

    await auth.signup(newcomer(), _request(invite['token']))
    saved = await User.find_one({'email': 'cy@example.com'})
    assert saved.role == 'member'  # the role comes from the invite, never from the form
    with pytest.raises(HTTPException):  # single use
        await auth.signup(User(name='Cy2', email='cy@example.com', password='long-enough-1'), _request(invite['token']))
    assert (await admin.list_invites(_ctx(people.ada)))[0]['state'] == 'used'


async def test_reset_link_by_email(people, monkeypatch):
    from cognitrix.api.routes import auth
    from cognitrix.common import email as mailer
    from cognitrix.common.security import authenticate

    with pytest.raises(HTTPException):  # email not set up
        await auth.forgot_password(auth.ForgotPassword(email='ada@example.com'), _request())

    sent = []

    async def fake_send(to, subject, body):
        sent.append((to, body))
    monkeypatch.setenv('COGNITRIX_SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('COGNITRIX_PUBLIC_URL', 'https://agents.example.com/')
    monkeypatch.setattr(mailer, 'send', fake_send)

    same = await auth.forgot_password(auth.ForgotPassword(email='nobody@example.com'), _request())
    assert sent == []
    assert await auth.forgot_password(auth.ForgotPassword(email='ada@example.com'), _request()) == same
    await auth.forgot_password(auth.ForgotPassword(email='ada@example.com'), _request())  # too soon: no second mail
    assert len(sent) == 1 and sent[0][0] == 'ada@example.com'
    token = sent[0][1].split('https://agents.example.com/reset-password?token=')[1].split()[0]

    with pytest.raises(HTTPException):
        await auth.reset_password(auth.ResetPassword(token='wrong', new_password='brand-new-1'))
    await auth.reset_password(auth.ResetPassword(token=token, new_password='brand-new-1'))
    assert await authenticate('ada@example.com', 'brand-new-1')
    with pytest.raises(HTTPException):  # works once
        await auth.reset_password(auth.ResetPassword(token=token, new_password='another-one-1'))


def test_smtp_message(monkeypatch):
    from cognitrix.common import email as mailer

    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(('connect', host, port))
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self, context=None): calls.append(('starttls',))
        def login(self, user, password): calls.append(('login', user))
        def send_message(self, message): calls.append(('send', message['To'], message['From'], message['Subject']))

    monkeypatch.setattr(mailer.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setenv('COGNITRIX_SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('COGNITRIX_SMTP_USER', 'robot@example.com')
    monkeypatch.setenv('COGNITRIX_SMTP_PASSWORD', 'pw')
    monkeypatch.delenv('COGNITRIX_SMTP_FROM', raising=False)
    monkeypatch.delenv('COGNITRIX_SMTP_PORT', raising=False)
    monkeypatch.delenv('COGNITRIX_SMTP_SECURITY', raising=False)
    mailer._send('ada@example.com', 'Hi', 'Body')
    assert calls == [('connect', 'smtp.example.com', 587), ('starttls',), ('login', 'robot@example.com'),
                     ('send', 'ada@example.com', 'robot@example.com', 'Hi')]
