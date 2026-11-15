"""Agent email: mail to an agent's address starts work, and it answers from it."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.agent_email import mail


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'email.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


def _raw(*, sender='me@example.com', to='agents+researcher@example.com', subject='Find flights',
         body='Please find flights to Accra.', auth='mx.example.com; dkim=pass header.d=example.com; spf=pass',
         message_id='<m1@example.com>', in_reply_to='', extra='') -> bytes:
    lines = []
    if auth:
        lines.append(f'Authentication-Results: {auth}')
    lines += [f'From: Me <{sender}>', f'To: {to}', f'Subject: {subject}', f'Message-ID: {message_id}']
    if in_reply_to:
        lines += [f'In-Reply-To: {in_reply_to}', f'References: {in_reply_to}']
    if extra:
        lines.append(extra)
    lines += ['Content-Type: text/plain; charset=utf-8', '', body]
    return '\r\n'.join(lines).encode()


# --- pure helpers ----------------------------------------------------------------

def test_addresses_per_agent():
    assert mail.agent_address('agents@example.com', 'plus', 'researcher') == 'agents+researcher@example.com'
    assert mail.agent_address('agents@example.com', 'domain', 'researcher') == 'researcher@example.com'
    assert mail.name_for('agents@example.com', 'plus', 'Agents+Researcher@Example.com') == 'researcher'
    assert mail.name_for('agents@example.com', 'plus', 'agents@example.com') == ''
    assert mail.name_for('agents@example.com', 'plus', 'other@example.com') is None
    assert mail.name_for('agents@example.com', 'plus', 'agents+x@elsewhere.com') is None
    assert mail.name_for('agents@example.com', 'domain', 'writer@example.com') == 'writer'
    assert mail.slug('Research Bot!') == 'research-bot'
    assert mail.valid_name('research-bot') and not mail.valid_name('-x') and not mail.valid_name('a b')


def test_sender_checks_need_a_pass_for_the_senders_own_domain():
    assert mail.authenticated('mx.google.com; dkim=pass header.i=@example.com', 'me@example.com')
    assert mail.authenticated('mx; spf=pass smtp.mailfrom=bounce@mail.example.com', 'me@example.com')
    assert mail.authenticated('mx; dmarc=pass (p=NONE) header.from=example.com', 'me@example.com')
    # A pass for another domain, a failure or no header proves nothing.
    assert not mail.authenticated('mx; dkim=pass header.d=evil.com; spf=pass smtp.mailfrom=x@evil.com',
                                  'me@example.com')
    assert not mail.authenticated('mx; dkim=fail header.d=example.com', 'me@example.com')
    assert not mail.authenticated('', 'me@example.com')
    assert mail.sender_listed('Me@Example.com', ['me@example.com'])
    assert mail.sender_listed('boss@corp.com', ['@corp.com'])
    assert not mail.sender_listed('boss@corp.com.evil.io', ['@corp.com'])


def test_parse_reads_the_new_text_and_spots_automated_mail():
    raw = _raw(body='Yes, book it.\n\nOn Mon, 1 Jan 2026, Agent <a@x.com> wrote:\n> Shall I book?\n')
    inbound = mail.parse(raw)
    assert inbound.sender == 'me@example.com' and inbound.message_id == '<m1@example.com>'
    assert 'agents+researcher@example.com' in inbound.recipients
    assert mail.new_text(inbound.body) == 'Yes, book it.'
    assert not inbound.automated
    assert mail.parse(_raw(extra='Auto-Submitted: auto-replied')).automated
    assert mail.parse(_raw(sender='mailer-daemon@example.com')).automated
    assert mail.parse(_raw(extra='List-Id: <news.example.com>')).automated
    # Only the top Authentication-Results counts: a sender can add fake ones below.
    forged = _raw(auth='mx.example.com; dkim=fail', extra='Authentication-Results: mx; dkim=pass header.d=example.com')
    assert not mail.authenticated(mail.parse(forged).auth_results, 'me@example.com')


def test_reply_mail_threads_and_marks_itself_automatic():
    msg = mail.build(sender='agents+r@example.com', sender_name='Researcher', to='me@example.com',
                     subject=mail.reply_subject('Find flights'), body='Done.', in_reply_to='<m1@example.com>',
                     references=['<m0@example.com>'])
    assert msg['Subject'] == 'Re: Find flights' and mail.reply_subject('RE: x') == 'RE: x'
    assert msg['In-Reply-To'] == '<m1@example.com>'
    assert msg['References'] == '<m0@example.com> <m1@example.com>'
    assert msg['Auto-Submitted'] == 'auto-replied'
    assert 'Researcher' in msg['From'] and msg['Message-ID'].endswith('@example.com>')


# --- the service -----------------------------------------------------------------

AGENTS = {'a1': SimpleNamespace(id='a1', name='Researcher'), 'a2': SimpleNamespace(id='a2', name='Writer')}


async def _setup(monkeypatch, **account_kw):
    from cognitrix.agent_email.models import AgentAddress, EmailAccount
    from cognitrix.agent_email.service import EmailService
    from cognitrix.agents import access
    from cognitrix.models import User

    person = User(name='Me', email='me@example.com', password='x' * 12)
    await person.save()
    person = await User.find_one({'email': 'me@example.com'})
    user = str(person.id)

    async def visible(user_key, agent_id=None, **_kw):
        return AGENTS.get(agent_id) if user_key == user else None

    monkeypatch.setattr(access, 'find_visible_agent', visible)
    account = EmailAccount(user_id=user, address='agents@example.com', imap_host='imap.example.com',
                           smtp_host='smtp.example.com', agent_id='a2', **account_kw)
    await account.save()
    account = await EmailAccount.find_one({'user_id': user})
    await AgentAddress(account_id=account.id, user_id=user, agent_id='a1', name='researcher').save()

    sent = []
    service = EmailService(sender=lambda message, **kw: sent.append(message))
    started = []

    async def fake_turn(thread, session, prompt):
        started.append((thread, str(session.id), prompt))

    service._turn = fake_turn
    return service, account, sent, started


async def _run(service):
    import asyncio

    for task in list(service._turns.values()):
        await asyncio.wait_for(task, 5)


async def test_mail_from_the_person_starts_work_with_the_addressed_agent(db, monkeypatch):
    service, account, sent, started = await _setup(monkeypatch)
    assert await service.handle(account, _raw()) == 'started'
    await _run(service)
    thread, session_id, prompt = started[0]
    assert thread.agent.id == 'a1' and thread.peer == 'me@example.com'
    assert 'Find flights' in prompt and 'Please find flights to Accra.' in prompt
    assert 'not the person you work for' not in prompt

    # The reply goes out from the agent's own address, in the same thread.
    await service.reply(thread, 'Found three.')
    assert sent[-1]['From'] == 'Researcher <agents+researcher@example.com>'
    assert sent[-1]['In-Reply-To'] == '<m1@example.com>'

    # Answering that reply continues the same chat.
    answer = _raw(message_id='<m2@example.com>', in_reply_to=sent[-1]['Message-ID'], to='agents+researcher@example.com')
    assert await service.handle(account, answer) == 'started'
    await _run(service)
    assert started[1][1] == session_id

    # The mailbox's own address goes to the chosen agent.
    assert await service.handle(account, _raw(to='agents@example.com', message_id='<m3@x>')) == 'started'
    await _run(service)
    assert started[2][0].agent.id == 'a2' and started[2][1] != session_id


async def test_strangers_forgeries_and_robots_get_nothing(db, monkeypatch):
    service, account, sent, started = await _setup(monkeypatch)
    assert await service.handle(account, _raw(sender='stranger@other.com',
                                              auth='mx; dkim=pass header.d=other.com')) == 'ignored'
    # A forged From line: right address, but nothing vouches for it.
    assert await service.handle(account, _raw(auth='')) == 'ignored'
    assert await service.handle(account, _raw(auth='mx; spf=fail; dkim=pass header.d=evil.com')) == 'ignored'
    assert await service.handle(account, _raw(extra='Auto-Submitted: auto-replied')) == 'automated'
    assert await service.handle(account, _raw(sender='agents+writer@example.com')) == 'own'
    assert await service.handle(account, _raw(to='someone@else.com')) == 'not-for-agent'
    assert await service.handle(account, _raw(to='agents+nobody@example.com')) == 'not-for-agent'
    assert started == [] and sent == []


async def test_other_people_get_a_read_only_chat_when_allowed(db, monkeypatch):
    service, account, sent, started = await _setup(monkeypatch, answer_others=True)
    assert await service.handle(account, _raw(sender='stranger@other.com', auth='')) == 'started'
    await _run(service)
    thread, _sid, prompt = started[0]
    assert thread.guest and 'not the person you work for' in prompt


async def test_allowed_senders_and_turning_off_the_check(db, monkeypatch):
    service, account, _sent, started = await _setup(
        monkeypatch, allowed_senders='@corp.com', require_auth=False)
    assert await service.handle(account, _raw(sender='boss@corp.com', auth='')) == 'started'
    # The list replaces the default: the person's own address isn't on it.
    assert await service.handle(account, _raw(message_id='<m9@x>')) == 'ignored'


async def test_a_reply_from_someone_else_in_the_agents_thread_goes_to_the_inbox(db, monkeypatch):
    from cognitrix.agent_email.models import EmailLink
    from cognitrix.tasks.automation import AgentNotification

    service, account, _sent, started = await _setup(monkeypatch)
    await EmailLink(account_id=account.id, message_id='<out@example.com>', session_id='s1', agent_id='a1',
                    subject='Quote', peer='vendor@shop.com').save()
    reply = _raw(sender='vendor@shop.com', auth='mx; dkim=pass header.d=shop.com', in_reply_to='<out@example.com>',
                 body='Our price is 40.', message_id='<v1@shop.com>')
    assert await service.handle(account, reply) == 'ignored'
    notes = await AgentNotification.find({'user_id': account.user_id})
    assert notes and 'vendor@shop.com replied to Researcher' == notes[0].title
    assert 'Our price is 40.' in notes[0].message and started == []


async def test_approve_by_reply(db, monkeypatch):
    from cognitrix.safety import approval_gate

    service, account, _sent, started = await _setup(monkeypatch)
    await service.handle(account, _raw())
    await _run(service)
    session_id = started[0][1]
    service._approvals[session_id] = 'web-approval-x'
    calls = []
    monkeypatch.setattr(approval_gate, 'resolve_web_approval',
                        lambda rid, approved, user_key=None: calls.append((rid, approved)) or True)
    assert await service.handle(account, _raw(body='approve\n\nOn Mon wrote:\n> x', message_id='<m5@x>',
                                              in_reply_to='<m1@example.com>')) == 'approved'
    assert calls == [('web-approval-x', True)]


async def test_poll_reads_only_new_mail(db, monkeypatch):
    from cognitrix.agent_email.models import EmailAccount

    service, account, _sent, started = await _setup(monkeypatch)
    calls = []

    def fetcher(**kw):
        calls.append(kw)
        if not kw['uid_validity']:
            return mail.Fetched('7', 41, [])
        return mail.Fetched('7', 42, [(42, _raw())])

    service._fetch = fetcher
    assert await service.poll_once(account) == 0
    account = await EmailAccount.get(account.id)
    assert (account.uid_validity, account.last_uid) == ('7', 41)
    assert await service.poll_once(account) == 1
    await _run(service)
    assert calls[1]['after_uid'] == 41 and len(started) == 1


# --- settings --------------------------------------------------------------------

def _ctx(user_id):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=SimpleNamespace(id=user_id, email=f'{user_id}@x.com'))


async def test_settings_check_servers_hide_the_password_and_give_each_agent_an_address(db, monkeypatch):
    from cognitrix.agent_email.models import EmailAccount
    from cognitrix.api.routes import agent_email as route
    from cognitrix.channels.service import ChannelService

    checked = []
    monkeypatch.setattr(mail, 'check_imap', lambda **kw: checked.append(('imap', kw['password'])))
    monkeypatch.setattr(mail, 'check_smtp', lambda **kw: checked.append(('smtp', kw['password'])))

    async def agents(self, user_key):
        return [SimpleNamespace(id='a1', name='Researcher'), SimpleNamespace(id='a2', name='Researcher')]

    monkeypatch.setattr(ChannelService, '_agents', agents)

    with pytest.raises(HTTPException) as err:
        await route.update_email(route.EmailUpdate(address='not an address'), _ctx('u1'))
    assert err.value.status_code == 400

    data = await route.update_email(route.EmailUpdate(
        address='Agents@Example.com', imap_host='imap.example.com', smtp_host='smtp.example.com',
        password='app-pass-1234'), _ctx('u1'))
    assert checked == [('imap', 'app-pass-1234'), ('smtp', 'app-pass-1234')]
    assert 'app-pass-1234' not in str(data)
    assert data['account']['password_set'] and data['account']['address'] == 'agents@example.com'
    row = await EmailAccount.find_one({'user_id': 'u1'})
    assert row.password != 'app-pass-1234' and row.password.startswith('enc:')
    assert [a['address'] for a in data['agents']] == ['agents+researcher@example.com',
                                                      'agents+researcher-2@example.com']

    with pytest.raises(HTTPException) as err:
        await route.update_address('a2', route.AddressUpdate(name='researcher'), _ctx('u1'))
    assert err.value.status_code == 409
    data = await route.update_address('a2', route.AddressUpdate(name='scout', enabled=False), _ctx('u1'))
    assert data['agents'][1] == {'agent_id': 'a2', 'agent_name': 'Researcher', 'name': 'scout',
                                 'enabled': False, 'address': 'agents+scout@example.com'}

    # Options alone don't re-check the servers.
    checked.clear()
    data = await route.update_email(route.EmailUpdate(answer_others=True, allowed_senders='me@x.com, @corp.com'),
                                    _ctx('u1'))
    assert checked == [] and data['account']['allowed_senders'] == 'me@x.com\n@corp.com'
    with pytest.raises(HTTPException):
        await route.update_email(route.EmailUpdate(allowed_senders='nonsense'), _ctx('u1'))

    # Another person can't take over the same mailbox.
    with pytest.raises(HTTPException) as err:
        await route.update_email(route.EmailUpdate(
            address='agents@example.com', imap_host='i', smtp_host='s', password='p'), _ctx('u2'))
    assert err.value.status_code == 409

    await route.delete_email(_ctx('u1'))
    assert (await route.get_email(_ctx('u1')))['account'] is None


async def test_catch_all_still_reads_mail_from_people_on_the_same_domain(db, monkeypatch):
    service, account, _sent, started = await _setup(monkeypatch, addressing='domain')
    # me@example.com is on the mailbox's domain but isn't the mailbox or an agent.
    assert await service.handle(account, _raw(to='researcher@example.com')) == 'started'
    assert await service.handle(account, _raw(sender='researcher@example.com', message_id='<o@x>')) == 'own'
    assert await service.handle(account, _raw(sender='agents@example.com', message_id='<o2@x>')) == 'own'


async def test_a_reply_picks_a_question_option_by_number_or_label(db, monkeypatch):
    from cognitrix.questions import broker

    service, account, _sent, started = await _setup(monkeypatch)
    await service.handle(account, _raw())
    await _run(service)
    sid = started[0][1]
    calls = []

    async def resolve(rid, user_key, action, **kw):
        calls.append(kw)
        return True

    monkeypatch.setattr(broker, 'resolve_question', resolve)
    for body, expected in (('2', {'option_id': 'o2'}), ('Window seat', {'option_id': 'o1'}),
                           ('something else', {'text': 'something else'})):
        service._questions[sid] = 'q1'
        service._question_options[sid] = [{'id': 'o1', 'label': 'Window seat'}, {'id': 'o2', 'label': 'Aisle'}]
        assert await service.handle(account, _raw(body=body, message_id=f'<q{len(calls)}@x>',
                                                  in_reply_to='<m1@example.com>')) == 'answered'
        assert calls[-1] == expected


async def test_mail_waits_for_a_turn_running_in_the_web_app(db, monkeypatch):
    from cognitrix.sessions import runs as chat_runs

    service, account, sent, started = await _setup(monkeypatch)
    await service.handle(account, _raw())
    await _run(service)
    busy = SimpleNamespace(running=True, steerable=False)
    monkeypatch.setattr(chat_runs, 'get_run', lambda user, sid: busy)
    assert await service.handle(account, _raw(message_id='<w@x>', in_reply_to='<m1@example.com>')) == 'busy'
    assert len(started) == 1 and 'Still working' in sent[-1].get_content()
    steered = []
    busy.steerable = True
    busy.steer = lambda text, source: steered.append(source) or object()
    assert await service.handle(account, _raw(message_id='<w2@x>', in_reply_to='<m1@example.com>')) == 'steered'
    assert steered == ['email']


def test_address_check_is_quick_on_hostile_input():
    import time

    assert mail.valid_address('a@b.co') and not mail.valid_address('a@b') and not mail.valid_address('a b@c.d')
    assert not mail.valid_address('a@@b.co') and not mail.valid_address('a@b..co')
    start = time.monotonic()
    mail.valid_address('!@!.' + '!.' * 50_000)
    assert time.monotonic() - start < 0.5
