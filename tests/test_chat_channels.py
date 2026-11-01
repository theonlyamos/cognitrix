"""Chat apps: Telegram and Slack bots linked to a person's agents."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from cognitrix.channels.base import BotInfo, ButtonPress, ChannelAuthError, Incoming, Transport
from cognitrix.channels.formatting import split_text, strip_reasoning, to_slack_mrkdwn, to_telegram_html


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'channels.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


class FakeTransport(Transport):
    kind = 'telegram'
    text_limit = 100

    def __init__(self, fail_check=False):
        self.sent: list[tuple[str, str, list]] = []
        self.edits: list[tuple[str, str, str]] = []
        self.acks: list[str] = []
        self.fail_check = fail_check

    async def check(self):
        if self.fail_check:
            raise ChannelAuthError('Telegram rejected the bot token')
        return BotInfo(name='@cog_bot')

    async def run(self, handlers):
        await handlers.on_connected()
        await asyncio.Event().wait()

    async def send(self, chat_id, text, buttons=None):
        self.sent.append((chat_id, text, list(buttons or [])))
        return str(len(self.sent))

    async def edit(self, chat_id, message_ref, text):
        self.edits.append((chat_id, message_ref, text))

    async def answer_button(self, press, text=''):
        self.acks.append(text)


async def _channel(user='u1', **kw):
    from cognitrix.models.chat_channel import ChatChannel

    kw.setdefault('kind', 'telegram')
    kw.setdefault('bot_token', '123:abc')
    kw.setdefault('link_code', 'ABCDEFGHJK')
    row = ChatChannel(user_id=user, **kw)
    await row.save()
    return await ChatChannel.get(row.id)


async def _service(channel):
    from cognitrix.channels.service import ChannelService

    transport = FakeTransport()
    service = ChannelService(transport_factory=lambda _row: transport)
    service._transports[channel.id] = transport
    service._connected.add(channel.id)
    return service, transport


def _msg(text, chat='c1', sender='p1', private=True):
    return Incoming(chat_id=chat, sender_id=sender, sender_name='Ada', text=text, private=private)


# --- formatting -------------------------------------------------------------


def test_telegram_html_escapes_and_converts_markdown():
    out = to_telegram_html('# Plan\n**bold** and *it* <x> [docs](https://e.com/a)\n```py\nif a<b: pass\n```\n`x<y`')
    assert '<b>Plan</b>' in out
    assert '<b>bold</b>' in out and '<i>it</i>' in out
    assert '&lt;x&gt;' in out
    assert '<a href="https://e.com/a">docs</a>' in out
    assert '<pre>if a&lt;b: pass</pre>' in out
    assert '<code>x&lt;y</code>' in out


def test_slack_mrkdwn_converts_markdown():
    out = to_slack_mrkdwn('**bold** *it* [docs](https://e.com) a<b `**raw**`')
    assert out == '*bold* _it_ <https://e.com|docs> a&lt;b `**raw**`'


def test_reasoning_blocks_are_not_sent():
    assert strip_reasoning('<think>plan\n</think>\nPONG') == 'PONG'
    assert strip_reasoning('Answer <think>still thinking') == 'Answer'


def test_split_text_respects_limit_and_prefers_paragraphs():
    text = 'a' * 60 + '\n\n' + 'b' * 60
    assert split_text(text, 100) == ['a' * 60, 'b' * 60]
    assert all(len(p) <= 10 for p in split_text('x' * 35, 10))
    assert split_text('   ', 10) == []


# --- linking ------------------------------------------------------------------


async def test_wrong_code_is_refused_and_right_code_links_the_chat(db):
    from cognitrix.models.chat_channel import ChatChannel

    channel = await _channel()
    service, transport = await _service(channel)

    await service._on_message(channel.id, _msg('hello'))
    assert 'isn\'t linked yet' in transport.sent[-1][1]
    assert (await ChatChannel.get(channel.id)).chat_id == ''

    await service._on_message(channel.id, _msg('/start abcdefghjk'))
    row = await ChatChannel.get(channel.id)
    assert (row.chat_id, row.peer_id, row.link_code) == ('c1', 'p1', '')
    assert any('Linked to Cognitrix' in text for _, text, _ in transport.sent)


async def test_group_chats_cannot_link(db):
    from cognitrix.models.chat_channel import ChatChannel

    channel = await _channel()
    service, transport = await _service(channel)
    await service._on_message(channel.id, _msg('ABCDEFGHJK', private=False))
    assert (await ChatChannel.get(channel.id)).chat_id == ''
    assert transport.sent == []


async def test_expired_code_and_too_many_guesses(db, monkeypatch):
    from cognitrix.channels import service as svc
    from cognitrix.models.chat_channel import ChatChannel

    channel = await _channel(link_code_expires_at='2000-01-01 00:00:00')
    service, _ = await _service(channel)
    await service._on_message(channel.id, _msg('ABCDEFGHJK'))
    assert (await ChatChannel.get(channel.id)).chat_id == ''

    channel = await _channel(user='u2', bot_token='9:z')
    service, _ = await _service(channel)
    monkeypatch.setattr(svc, 'MAX_LINK_ATTEMPTS', 3)
    for _ in range(3):
        await service._on_message(channel.id, _msg('nope'))
    assert (await ChatChannel.get(channel.id)).link_code == ''


async def test_strangers_are_turned_away_once_linked(db):
    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    service._turn = None  # a turn must not start
    await service._on_message(channel.id, _msg('hi', chat='c9', sender='p9'))
    assert transport.sent[-1][1] == 'This bot is private.'
    await service._on_message(channel.id, _msg('hi', chat='g1', sender='p9', private=False))
    assert len(transport.sent) == 1


async def test_new_command_clears_the_conversation(db):
    from cognitrix.models.chat_channel import ChatChannel

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='', session_id='s1')
    service, transport = await _service(channel)
    service._agent = lambda _c: _async(SimpleNamespace(name='Helper', id='a1'))
    await service._on_message(channel.id, _msg('/new'))
    assert (await ChatChannel.get(channel.id)).session_id == ''
    assert 'new conversation with Helper' in transport.sent[-1][1]


async def _async(value):
    return value


# --- chat turns ---------------------------------------------------------------


class FakeSession:
    def __init__(self, approve_tool=False):
        self.id = 's1'
        self.chat = []
        self.approve_tool = approve_tool
        self.approval = None

    async def __call__(self, text, agent, *, output, **kwargs):
        await output({'type': 'generate', 'content': 'Hello '})
        await output({'type': 'tool', 'status': 'started', 'tool_name': 'x'})
        if self.approve_tool:
            from cognitrix.safety.approval_gate import ApprovalGate, ToolCall
            from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

            gate = ApprovalGate.__new__(ApprovalGate)
            self.approval = await gate._web_approval(
                ToolCall('Delete File', {'path': 'a.txt'}),
                RiskAssessment(risk_level=RiskLevel.HIGH, categories=['delete'], details='deletes'),
                5.0,
            )
        await output({'type': 'generate', 'content': f'you said {text}'})

    async def save(self):
        pass


async def test_message_runs_a_turn_and_sends_the_reply(db):
    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    session = FakeSession()
    service._agent = lambda _c: _async(SimpleNamespace(name='Helper', id='a1'))
    service._session = lambda _c, _a: _async(session)

    await service._on_message(channel.id, _msg('ping'))
    await service._turns[channel.id]
    assert transport.sent[-1][1] == 'Hello you said ping'


async def test_risky_tool_call_asks_with_buttons_and_the_button_answers(db):
    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    session = FakeSession(approve_tool=True)
    service._agent = lambda _c: _async(SimpleNamespace(name='Helper', id='a1'))
    service._session = lambda _c, _a: _async(session)

    await service._on_message(channel.id, _msg('clean up'))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(buttons for _, _, buttons in transport.sent):
            break
    chat, text, buttons = next(item for item in transport.sent if item[2])
    assert 'Delete File' in text and [b.label for b in buttons] == ['Approve', 'Deny']
    assert transport.sent[0][1] == 'Hello'  # text before the request is sent first

    # Someone else in the chat can't answer.
    await service._on_button(channel.id, ButtonPress('c1', 'p9', buttons[0].data, '2'))
    assert transport.acks[-1].startswith('Only the linked account')

    await service._on_button(channel.id, ButtonPress('c1', 'p1', buttons[0].data, '2'))
    await service._turns[channel.id]
    assert session.approval.approved is True
    assert transport.edits and transport.edits[-1][2].endswith('Approved.')
    assert transport.sent[-1][1] == 'you said clean up'


# --- Inbox delivery -------------------------------------------------------------


async def test_alerts_and_task_approvals_are_delivered_once_and_answerable(db):
    from cognitrix.tasks import approvals
    from cognitrix.tasks.automation import create_notification

    await create_notification('u1', 'Old alert', 'from before linking')
    channel = await _channel(link_code='ABCDEFGHJK')
    service, transport = await _service(channel)
    await service._on_message(channel.id, _msg('ABCDEFGHJK'))
    transport.sent.clear()

    await create_notification('u1', 'Build failed', 'main is red')
    await create_notification('someone-else', 'Not yours', '')
    row = approvals.TaskApproval(run_id='r1', task_id=None, user_id='u1', tool_name='Write',
                                 params={'path': 'x'}, risk_level='medium')
    await row.save()

    assert await service.deliver_once() == 2
    texts = [text for _, text, _ in transport.sent]
    assert any('Build failed' in t and 'main is red' in t for t in texts)
    assert not any('Old alert' in t or 'Not yours' in t for t in texts)
    approval_msg = next(item for item in transport.sent if item[2])
    assert await service.deliver_once() == 0

    approve = approval_msg[2][0]
    await service._on_button(channel.id, ButtonPress('c1', 'p1', approve.data, '3'))
    assert (await approvals.TaskApproval.get(row.id)).status == approvals.APPROVED
    assert transport.acks[-1] == 'Approved.'


async def test_approval_answered_elsewhere_loses_its_buttons(db):
    from cognitrix.tasks import approvals

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    row = approvals.TaskApproval(run_id='r1', user_id='u1', tool_name='Write', params={})
    await row.save()
    await service.deliver_once()
    ref = next(str(i + 1) for i, item in enumerate(transport.sent) if item[2])

    await approvals.decide(row.id, 'r1', False)
    await service.deliver_once()
    assert transport.edits[-1][1] == ref and transport.edits[-1][2].endswith('Denied.')


async def test_alerts_can_be_turned_off(db):
    from cognitrix.tasks.automation import create_notification

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='', send_alerts=False)
    service, transport = await _service(channel)
    await create_notification('u1', 'Quiet', '')
    assert await service.deliver_once() == 0


async def test_a_task_approval_of_another_user_cannot_be_answered(db):
    from cognitrix.tasks import approvals

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    row = approvals.TaskApproval(run_id='r1', user_id='u2', tool_name='Write', params={})
    await row.save()
    await service._on_button(channel.id, ButtonPress('c1', 'p1', f'ta:{row.id}:1', '5'))
    assert (await approvals.TaskApproval.get(row.id)).status == approvals.PENDING


# --- settings API ----------------------------------------------------------------


def _ctx(user_id):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=SimpleNamespace(id=user_id, email=f'{user_id}@x.com'))


async def test_settings_check_tokens_hide_them_and_issue_a_link_code(db, monkeypatch):
    from cognitrix.api.routes import channels
    from cognitrix.channels import service as svc

    monkeypatch.setattr(svc, 'make_transport', lambda row: FakeTransport(fail_check=row.bot_token == 'bad'))

    with pytest.raises(HTTPException) as err:
        await channels.update_channel('telegram', channels.ChannelUpdate(bot_token='bad'), _ctx('u1'))
    assert err.value.status_code == 400

    data = await channels.update_channel(
        'telegram', channels.ChannelUpdate(bot_token=' 123:secret-token '), _ctx('u1'))
    assert data['configured'] and data['bot_name'] == '@cog_bot' and not data['linked']
    assert len(data['link_code']) == 10
    assert data['link_url'] == f'https://t.me/cog_bot?start={data["link_code"]}'
    assert 'secret-token' not in json.dumps(data)

    listed = await channels.list_channels(_ctx('u1'))
    assert [c['kind'] for c in listed['channels']] == ['telegram', 'slack']
    assert 'secret-token' not in json.dumps(listed)
    other = await channels.list_channels(_ctx('u2'))
    assert other['channels'][0]['configured'] is False

    with pytest.raises(HTTPException) as err:
        await channels.update_channel('telegram', channels.ChannelUpdate(bot_token='123:secret-token'), _ctx('u2'))
    assert err.value.status_code == 409

    with pytest.raises(HTTPException) as err:
        await channels.update_channel('slack', channels.ChannelUpdate(bot_token='xoxb-1'), _ctx('u1'))
    assert 'app-level token' in err.value.detail

    data = await channels.update_channel('telegram', channels.ChannelUpdate(send_alerts=False), _ctx('u1'))
    assert data['send_alerts'] is False and data['link_code']

    await channels.delete_channel('telegram', _ctx('u1'))
    assert (await channels.list_channels(_ctx('u1')))['channels'][0]['configured'] is False


async def test_settings_reject_agents_the_user_cannot_see(db, monkeypatch):
    from cognitrix.api.routes import channels
    from cognitrix.channels import service as svc

    monkeypatch.setattr(svc, 'make_transport', lambda row: FakeTransport())
    with pytest.raises(HTTPException) as err:
        await channels.update_channel(
            'telegram', channels.ChannelUpdate(bot_token='1:a', agent_id='missing'), _ctx('u1'))
    assert err.value.status_code == 404


# --- transports ----------------------------------------------------------------------


async def test_telegram_transport_parses_updates_and_falls_back_to_plain_text():
    from cognitrix.channels.telegram import TelegramTransport

    calls = []

    def handler(request: httpx.Request):
        method = request.url.path.rsplit('/', 1)[-1]
        body = json.loads(request.content or b'{}')
        calls.append((method, body))
        if method == 'getMe':
            return httpx.Response(200, json={'ok': True, 'result': {'username': 'cog_bot'}})
        if method == 'sendMessage' and body.get('parse_mode'):
            return httpx.Response(400, json={'ok': False, 'description': "can't parse entities"})
        if method == 'sendMessage':
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': 7}})
        return httpx.Response(401, json={'ok': False})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = TelegramTransport('1:tok', client=client)
    info = await transport.check()
    assert info.name == '@cog_bot' and info.link_url == 'https://t.me/cog_bot'
    assert await transport.send('42', '**hi**') == '7'
    assert calls[-1][1]['text'] == '**hi**' and 'parse_mode' not in calls[-1][1]
    with pytest.raises(ChannelAuthError):
        await transport._call('getUpdates')
    await transport.typing('42')  # never raises

    got = []

    async def on_message(msg):
        got.append(msg)

    async def on_button(press):
        got.append(press)

    from cognitrix.channels.base import Handlers

    handlers = Handlers(on_message=on_message, on_button=on_button)
    await transport._dispatch({'message': {'text': 'yo', 'chat': {'id': 5, 'type': 'private'},
                                           'from': {'id': 9, 'first_name': 'Ada'}}}, handlers)
    await transport._dispatch({'callback_query': {'id': 'q', 'data': 'ta:1:1', 'from': {'id': 9},
                                                  'message': {'message_id': 3, 'chat': {'id': 5}}}}, handlers)
    assert got[0] == Incoming('5', '9', 'Ada', 'yo', True)
    assert got[1] == ButtonPress('5', '9', 'ta:1:1', '3', 'q')


async def test_slack_transport_ignores_bot_messages_and_reads_buttons():
    from cognitrix.channels.base import Handlers
    from cognitrix.channels.slack import SlackTransport

    transport = SlackTransport('xoxb-1', 'xapp-1')
    transport._bot_user_id = 'UBOT'
    got = []

    async def collect(item):
        got.append(item)

    handlers = Handlers(on_message=collect, on_button=collect)
    for event in (
        {'type': 'message', 'user': 'UBOT', 'text': 'echo', 'channel': 'D1', 'channel_type': 'im'},
        {'type': 'message', 'bot_id': 'B1', 'user': 'U2', 'text': 'bot', 'channel': 'D1'},
        {'type': 'message', 'subtype': 'message_changed', 'channel': 'D1'},
        {'type': 'message', 'user': 'U1', 'text': 'hi', 'channel': 'D1', 'channel_type': 'im'},
    ):
        await transport._dispatch({'type': 'events_api', 'payload': {'event': event}}, handlers)
    await transport._dispatch({'type': 'interactive', 'payload': {
        'type': 'block_actions', 'user': {'id': 'U1'},
        'container': {'channel_id': 'D1', 'message_ts': '17.1'},
        'actions': [{'value': 'ta:1:0'}],
    }}, handlers)
    assert got == [Incoming('D1', 'U1', '', 'hi', True), ButtonPress('D1', 'U1', 'ta:1:0', '17.1')]
    blocks = transport._blocks('Approve?', [])
    assert blocks is None
    await transport.close()


async def test_button_text_is_scoped_to_its_channel(db):
    from cognitrix.channels.base import Button

    first = await _channel(chat_id='c1', peer_id='p1', link_code='')
    second = await _channel(user='u2', bot_token='9:z', chat_id='c2', peer_id='p2', link_code='')
    service, transport = await _service(first)
    service._transports[second.id] = transport
    await service._reply(first, 'c1', 'secret params of u1', [Button('Deny', 'ca:x:0')])
    ref = transport.sent[-1]
    # The second channel gets a message with the same id from its own chat.
    await service._on_button(second.id, ButtonPress('c2', 'p2', 'ca:missing:0', '1'))
    assert 'secret params' not in transport.edits[-1][2]
    assert ref[1] == 'secret params of u1'


async def test_relinking_stops_the_running_reply_and_sends_nothing_to_the_old_chat(db):
    from cognitrix.api.routes import channels
    from cognitrix.channels import service as svc

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    started = asyncio.Event()

    class SlowSession(FakeSession):
        async def __call__(self, text, agent, *, output, **kwargs):
            await output({'type': 'generate', 'content': 'partial'})
            started.set()
            await asyncio.sleep(30)

    service._agent = lambda _c: _async(SimpleNamespace(name='Helper', id='a1'))
    service._session = lambda _c, _a: _async(SlowSession())
    svc._service = service
    try:
        await service._on_message(channel.id, _msg('long job'))
        await started.wait()
        await channels.relink_channel('telegram', _ctx('u1'))
        assert channel.id not in service._turns
        assert transport.sent == []
    finally:
        svc._service = None
