"""Sharing an agent with roles, chat app mentions (Slack channels, Microsoft
Teams), and meeting notes from recordings."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
from cognitrix.agents import access
from cognitrix.api.routes import agents as agent_routes
from cognitrix.api.routes import assistant
from cognitrix.common.security import AuthContext
from cognitrix.models import Agent, User
from cognitrix.providers.base import LLM
from cognitrix.tasks import standing


def _llm():
    return LLM(provider='openai', base_url='http://x', api_key='k', model='m')


def _ctx(user='u1', role='member'):
    return AuthContext(user=SimpleNamespace(id=user, role=role, email=f'{user}@x.test'), api_key=None)


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.models.chat_channel import ChannelDelivery, ChannelThread, ChatChannel
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.base import Task

    db_file = str(tmp_path / 'team.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (Agent, User, Task, ChatChannel, ChannelThread, ChannelDelivery, Session, SessionOwnership):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()


async def _agent(owner='u1', **kw):
    agent = Agent(name=kw.pop('name', 'Ada'), llm=_llm(), system_prompt='sys', owner_id=owner, **kw)
    await agent.save()
    return agent


@pytest.fixture
def people(monkeypatch):
    rows = [SimpleNamespace(id=f'u{i}', name=f'Person {i}', email=f'u{i}@x.test', disabled=False) for i in (1, 2, 3)]
    rows.append(SimpleNamespace(id='u9', name='Gone', email='u9@x.test', disabled=True))

    async def everyone():
        return [r for r in rows if not r.disabled]

    monkeypatch.setattr(agent_routes, '_people', everyone)
    return rows


# --- sharing an agent ---------------------------------------------------------

def test_share_roles():
    agent = SimpleNamespace(owner_id='u1', shared_with={'u2': 'use', 'u3': 'edit', 'u4': 'bogus'})
    assert access.can_view(agent, 'u1') and access.can_view(agent, 'u2') and access.can_view(agent, 'u3')
    assert not access.can_view(agent, 'u4') and not access.can_view(agent, None)
    assert access.share_role(agent, 'u2') == 'use' and access.share_role(agent, 'u3') == 'edit'
    everyone = SimpleNamespace(owner_id='u1', shared_with={'*': 'use', 'u3': 'edit'})
    assert access.share_role(everyone, 'u7') == 'use' and access.share_role(everyone, 'u3') == 'edit'
    # tasks and teams have no shares: still owner only
    assert not access.can_view(SimpleNamespace(owner_id='u1'), 'u2')


async def test_owner_shares_and_roles_limit_what_people_can_do(db, people):
    agent = await _agent('u1', mcp_servers=[], description='Mine')
    owner, user, editor, stranger = _ctx('u1'), _ctx('u2'), _ctx('u3'), _ctx('u4')

    with pytest.raises(HTTPException) as exc:
        await agent_routes.load_agent(str(agent.id), user)
    assert exc.value.status_code == 404

    view = await agent_routes.set_sharing(str(agent.id), agent_routes.SharingUpdate(people=[
        agent_routes.ShareEntry(user_id='u2', role='use'),
        agent_routes.ShareEntry(user_id='u3', role='edit'),
        agent_routes.ShareEntry(user_id='u1', role='edit'),  # the owner is skipped
    ]), owner)
    assert {p['id']: p['role'] for p in view['people']} == {'u2': 'use', 'u3': 'edit'}
    assert [p['id'] for p in view['directory']] == ['u2', 'u3']  # not me, not disabled accounts

    seen = await agent_routes.load_agent(str(agent.id), user)
    assert seen['my_role'] == 'use' and seen['shared_with'] == {} and seen['shared'] is True
    assert (await agent_routes.load_agent(str(agent.id), editor))['my_role'] == 'edit'
    assert (await agent_routes.load_agent(str(agent.id), owner))['my_role'] == 'owner'
    assert [a['id'] for a in await agent_routes.list_agents(user)] == [str(agent.id)]
    assert await agent_routes.list_agents(stranger) == []

    # people it is shared with see their role, not who else has it
    mine = await agent_routes.get_sharing(str(agent.id), user)
    assert mine['role'] == 'use' and mine['owner']['id'] == 'u1' and 'people' not in mine

    # only the owner shares or deletes
    for who in (user, editor):
        with pytest.raises(HTTPException) as exc:
            await agent_routes.set_sharing(str(agent.id), agent_routes.SharingUpdate(everyone='edit'), who)
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            await agent_routes.delete_agent(str(agent.id), who)
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await agent_routes.set_sharing(str(agent.id), agent_routes.SharingUpdate(everyone='edit'), stranger)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:  # unknown people and roles are refused
        await agent_routes.set_sharing(str(agent.id), agent_routes.SharingUpdate(
            people=[agent_routes.ShareEntry(user_id='nobody', role='use')]), owner)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await agent_routes.set_sharing(str(agent.id), agent_routes.SharingUpdate(everyone='owner'), owner)
    assert exc.value.status_code == 400

    view = await agent_routes.set_sharing(str(agent.id), agent_routes.SharingUpdate(everyone='use'), owner)
    assert view['everyone'] == 'use' and view['people'] == []
    assert (await agent_routes.load_agent(str(agent.id), stranger))['my_role'] == 'use'


class _Request:
    def __init__(self, data):
        self._data = data
        self.state = SimpleNamespace(agent=None)

    async def json(self):
        return self._data


async def _save(agent, ctx, **changes):
    data = {**agent.json(), **changes, 'llm': {'provider': 'openai', 'model': 'm', 'base_url': 'http://x', 'api_key': '***'}}
    body = Agent(**{k: v for k, v in data.items() if k != 'llm'}, llm=_llm())
    return await agent_routes.save_agent(_Request(data), body, ctx)


async def test_editor_changes_instructions_but_not_what_the_agent_reaches(db, people, tmp_path):
    agent = await _agent('u1', folders=[{'path': str(tmp_path), 'access': 'write', 'name': 'Owner', 'primary': True}],
                         credentials=['GITHUB'], shared_with={'u2': 'use', 'u3': 'edit'})

    with pytest.raises(HTTPException) as exc:
        await _save(agent, _ctx('u2'), system_prompt='hijacked')
    assert exc.value.status_code == 403

    out = await _save(agent, _ctx('u3'), system_prompt='Be brief.', folders=[], credentials=[],
                      shared_with={'*': 'edit'}, owner_id='u3', mcp_servers=['someone-elses'])
    assert out['system_prompt'] == 'Be brief.' and out['my_role'] == 'edit'
    stored = await Agent.find_one({'id': agent.id})
    assert stored.system_prompt == 'Be brief.'
    assert stored.owner_id == 'u1' and stored.shared_with == {'u2': 'use', 'u3': 'edit'}
    assert stored.credentials == ['GITHUB'] and stored.mcp_servers == []
    assert stored.folders and stored.folders[0]['path'] == str(tmp_path)
    assert stored.llm.api_key == 'k'  # the owner's key stays for the same endpoint

    # the owner's own save keeps the shares too
    data = {k: v for k, v in stored.json().items() if k != 'folders'}
    data.update(description='Team helper', shared_with={},
                llm={'provider': 'openai', 'model': 'm', 'base_url': 'http://x', 'api_key': '***'})
    await agent_routes.save_agent(
        _Request(data), Agent(**{k: v for k, v in data.items() if k != 'llm'}, llm=_llm()), _ctx('u1'))
    assert (await Agent.find_one({'id': agent.id})).shared_with == {'u2': 'use', 'u3': 'edit'}


async def test_check_ins_stay_with_the_owner(db, people):
    agent = await _agent('u1', shared_with={'u3': 'edit'},
                         automation={'standing': {'enabled': False, 'primary': True, 'brief': {'keep_true': 'x'}}})
    editor = _ctx('u3')
    seen = await agent_routes.load_agent(str(agent.id), editor)
    assert 'standing' not in (seen.get('automation') or {})
    with pytest.raises(HTTPException) as exc:
        await assistant.get_standing(str(agent.id), editor)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await assistant.save_standing(str(agent.id), assistant.StandingBody(enabled=True), editor)
    assert exc.value.status_code == 403
    # someone else's "main assistant" mark never makes it mine
    own = await _agent('u3', name='Mine')
    assert standing.primary_first([own, agent], 'u3')[0].name == 'Mine'
    assert standing.primary_first([own, agent], 'u1')[0].name == 'Ada'
    assert standing.primary_first([own, agent])[0].name == 'Ada'


# --- Slack channel mentions and Microsoft Teams --------------------------------


class _ThreadTransport:
    kind = 'slack'
    text_limit = 3000

    def __init__(self):
        self.sent = []
        self.acks = []

    async def send(self, chat_id, text, buttons=None, thread_id=''):
        self.sent.append((chat_id, thread_id, text))
        return str(len(self.sent))

    async def typing(self, chat_id):
        pass

    async def edit(self, chat_id, ref, text):
        pass

    async def answer_button(self, press, text=''):
        self.acks.append(text)


class _SeenSession:
    """Stands in for a chat session and records the limits its turn ran under."""

    def __init__(self, sid):
        self.id = sid
        self.chat = []
        self.seen = {}

    async def __call__(self, text, agent, *, output, **kwargs):
        from cognitrix.safety.permissions import guest_run, read_only_run

        self.seen = {'text': text, 'read_only': read_only_run.get(), 'guest': guest_run.get()}
        await output({'type': 'generate', 'content': 'answer'})

    async def save(self):
        pass


async def _mention_rig(**channel_fields):
    from cognitrix.channels.service import ChannelService
    from cognitrix.models.chat_channel import ChatChannel

    row = ChatChannel(user_id='u1', kind='slack', bot_token='xoxb', app_token='xapp',
                      chat_id='D1', peer_id='UOWNER', peer_name='Ama', **channel_fields)
    await row.save()
    transport = _ThreadTransport()
    service = ChannelService(transport_factory=lambda _row: transport)
    service._transports[row.id] = transport
    service._connected.add(row.id)
    sessions = []

    async def agent(_channel):
        return SimpleNamespace(name='Helper', id='a1')

    async def thread_session(_channel, _agent, chat_id, thread_id, guest):
        sessions.append(_SeenSession(f's{len(sessions)}'))
        sessions[-1].where = (chat_id, thread_id, guest)
        return sessions[-1]

    service._agent = agent
    service._thread_session = thread_session
    return row, service, transport, sessions


async def test_slack_mention_in_a_channel_becomes_a_threaded_message():
    from cognitrix.channels.base import Handlers, Incoming
    from cognitrix.channels.slack import SLACK_MANIFEST, SlackTransport

    transport = SlackTransport('xoxb-1', 'xapp-1')
    transport._bot_user_id = 'UBOT'
    got = []

    async def collect(item):
        got.append(item)

    handlers = Handlers(on_message=collect, on_button=collect)
    for event in (
        {'type': 'app_mention', 'user': 'U1', 'text': '<@UBOT> what changed?', 'channel': 'C1', 'ts': '10.1'},
        {'type': 'app_mention', 'user': 'U2', 'text': 'and <@UBOT> then?', 'channel': 'C1',
         'ts': '10.5', 'thread_ts': '10.1'},
        {'type': 'app_mention', 'user': 'UBOT', 'text': '<@UBOT> loop', 'channel': 'C1', 'ts': '10.6'},
    ):
        await transport._dispatch({'type': 'events_api', 'payload': {'event': event}}, handlers)
    assert got == [
        Incoming('C1', 'U1', '', 'what changed?', False, True, '10.1'),
        Incoming('C1', 'U2', '', 'and   then?', False, True, '10.1'),
    ]
    assert 'app_mention' in SLACK_MANIFEST['settings']['event_subscriptions']['bot_events']
    assert 'app_mentions:read' in SLACK_MANIFEST['oauth_config']['scopes']['bot']
    await transport.close()


async def test_owner_mention_is_answered_in_the_thread_with_full_powers(db):
    from cognitrix.channels.base import Incoming

    row, service, transport, sessions = await _mention_rig()
    await service._on_message(row.id, Incoming('C9', 'UOWNER', 'Ama', 'status?', False, True, '11.1'))
    await service._turns[f'{row.id}|C9|11.1']
    assert transport.sent == [('C9', '11.1', 'answer')]
    assert sessions[0].where == ('C9', '11.1', False)
    assert sessions[0].seen['read_only'] == '' and sessions[0].seen['guest'] is False
    assert 'everyone there reads your reply' in sessions[0].seen['text']


async def test_other_people_are_refused_unless_allowed_and_then_only_read(db):
    from cognitrix.channels.base import ButtonPress, Incoming
    from cognitrix.models.chat_channel import ChatChannel

    row, service, transport, sessions = await _mention_rig()
    stranger = Incoming('C9', 'UOTHER', 'Kofi', 'what is our refund policy?', False, True, '12.1')
    await service._on_message(row.id, stranger)
    assert sessions == [] and transport.sent == [('C9', '12.1', 'I only answer Ama here.')]

    await ChatChannel.update_one({'id': row.id}, {'answer_others': True})
    await service._on_message(row.id, stranger)
    await service._turns[f'{row.id}|C9|12.1']
    assert transport.sent[-1] == ('C9', '12.1', 'answer')
    seen = sessions[0].seen
    assert sessions[0].where == ('C9', '12.1', True)
    assert seen['guest'] is True and seen['read_only']
    assert 'Kofi asked this' in seen['text'] and 'not Ama' in seen['text']

    # Buttons in a shared channel stay the linked person's.
    await service._on_button(row.id, ButtonPress('C9', 'UOTHER', 'ca:x:1', '3'))
    assert transport.acks[-1].startswith('Only the linked account')


async def test_mentions_are_ignored_where_people_cannot_be_told_apart(db):
    from cognitrix.channels.base import Incoming
    from cognitrix.models.chat_channel import ChatChannel

    row, service, transport, sessions = await _mention_rig(answer_others=True)
    await ChatChannel.update_one({'id': row.id}, {'kind': 'telegram'})
    await service._on_message(row.id, Incoming('G1', 'UOWNER', 'Ama', 'hi', False, True, ''))
    assert sessions == [] and transport.sent == []


async def test_thread_conversations_are_kept_apart_from_the_owners(db):
    from cognitrix.agents import access
    from cognitrix.channels.service import ChannelService
    from cognitrix.models.chat_channel import ChannelThread, ChatChannel
    from cognitrix.sessions.base import Session

    agent = await _agent(owner='u1')
    row = ChatChannel(user_id='u1', kind='slack', bot_token='t', chat_id='D1', peer_id='UOWNER')
    await row.save()
    service = ChannelService()
    first = await service._thread_session(row, agent, 'C9', '1.1', True)
    again = await service._thread_session(row, agent, 'C9', '1.1', True)
    assert str(first.id) == str(again.id) and first.incognito is True
    # The owner joining a thread a guest started gets their own conversation.
    own = await service._thread_session(row, agent, 'C9', '1.1', False)
    assert str(own.id) != str(first.id) and not own.incognito
    assert len(await ChannelThread.find({'channel_id': row.id})) == 1
    assert (await ChatChannel.get(row.id)).session_id == ''
    assert await Session.get(str(own.id)) is not None and access is not None


def test_guests_cannot_reach_connected_apps_memory_or_the_shell():
    from cognitrix.safety import permissions as perms

    def looks(name, category='', args=None):
        return perms._only_looks(SimpleNamespace(name=name, category=category), args or {}, True)

    assert looks('Read') and looks('Gmail Search', 'mcp') and looks('Search Chats')
    token = perms.guest_run.set(True)
    try:
        assert looks('Read') and looks('Search Knowledge') and looks('WebFetch')
        for name, category, args in (
            ('Gmail Search', 'mcp', {}), ('Search Chats', '', {}), ('List Credentials', '', {}),
            ('Bash', '', {'command': 'ls'}), ('Take Screenshot', '', {}), ('Notify Me', '', {}),
        ):
            assert not looks(name, category, args), name
    finally:
        perms.guest_run.reset(token)


def _teams_rig():
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from cognitrix.channels.teams import TeamsTransport

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({'kid': 'k1', 'endorsements': ['msteams']})
    transport = TeamsTransport('app-1', 'secret', 'tenant-1')
    transport._keys = {'k1': jwk}
    transport._keys_at = time.time()

    def token(**claims):
        body = {'iss': 'https://api.botframework.com', 'aud': 'app-1', 'exp': int(time.time()) + 600,
                'serviceurl': 'https://smba.example/amer/', **claims}
        return 'Bearer ' + jwt.encode(body, key, algorithm='RS256', headers={'kid': 'k1'})

    def activity(**fields):
        return {'type': 'message', 'channelId': 'msteams', 'serviceUrl': 'https://smba.example/amer/',
                'conversation': {'id': '19:abc@thread.tacv2;messageid=1', 'conversationType': 'channel',
                                 'tenantId': 'tenant-1'},
                'from': {'id': '29:x', 'aadObjectId': 'aad-1', 'name': 'Ama'},
                'text': '<at>Cognitrix</at> what is new?', **fields}

    return transport, token, activity


async def test_teams_only_accepts_calls_signed_for_this_bot_and_organisation():
    from cognitrix.channels.base import Handlers, Incoming
    from cognitrix.channels.teams import TeamsAuthError

    transport, token, activity = _teams_rig()
    got = []

    async def collect(item):
        got.append(item)

    transport._handlers = Handlers(on_message=collect, on_button=collect)
    await transport.receive(token(), activity())
    assert got == [Incoming('19:abc@thread.tacv2;messageid=1', 'aad-1', 'Ama', 'what is new?', False, True)]
    await transport.receive(token(), activity(conversation={
        'id': 'a:1', 'conversationType': 'personal', 'tenantId': 'tenant-1'}, text='LINKCODE'))
    assert got[-1].private is True and got[-1].mention is False
    await transport.receive(token(), activity(value={'cx': 'ca:r1:1'}, replyToId='m7', text=''))
    assert got[-1].data == 'ca:r1:1' and got[-1].message_ref == 'm7' and got[-1].sender_id == 'aad-1'

    for bad_token, bad_activity in (
        ('', activity()),
        (token(aud='someone-else'), activity()),
        (token(iss='https://evil.example'), activity()),
        (token(exp=int(time.time()) - 4000), activity()),
        (token(serviceurl='https://evil.example/'), activity()),
        (token(), activity(serviceUrl='https://evil.example/')),
        (token(), activity(channelId='webchat')),
        (token(), activity(conversation={'id': 'x', 'tenantId': 'another-org'})),
        (token()[:-6] + 'AAAAAA', activity()),
    ):
        with pytest.raises(TeamsAuthError):
            await transport.receive(bad_token, bad_activity)
    assert len(got) == 3
    await transport.close()


async def test_teams_endpoint_turns_away_unsigned_calls(db):
    from cognitrix.api.routes import channels as routes
    from cognitrix.channels import service as channel_service
    from cognitrix.channels.service import ChannelService

    transport, token, activity = _teams_rig()
    service = ChannelService()
    service._transports['ch1'] = transport

    class Req:
        def __init__(self, auth, body):
            self.headers = {'authorization': auth} if auth else {}
            self._body = json.dumps(body).encode()

        async def body(self):
            return self._body

    old = channel_service._service
    channel_service._service = service
    try:
        with pytest.raises(HTTPException) as err:
            await routes.teams_messages('ch1', Req('', activity()))
        assert err.value.status_code == 401
        with pytest.raises(HTTPException) as err:
            await routes.teams_messages('nope', Req(token(), activity()))
        assert err.value.status_code == 404
        assert (await routes.teams_messages('ch1', Req(token(), activity()))).status_code == 200
    finally:
        channel_service._service = old
        await transport.close()


async def test_teams_settings_need_all_three_values_and_show_the_endpoint(db, monkeypatch):
    from cognitrix.api.routes import channels as routes
    from cognitrix.channels.base import BotInfo

    class Probe:
        async def check(self):
            return BotInfo(name='Teams bot')

        async def close(self):
            pass

    monkeypatch.setattr(routes.channel_service, 'make_transport', lambda _row: Probe())
    ctx = _ctx('u1')
    with pytest.raises(HTTPException) as err:
        await routes.update_channel('teams', routes.ChannelUpdate(bot_token='secret', app_id='app-1'), ctx)
    assert err.value.status_code == 400
    saved = await routes.update_channel('teams', routes.ChannelUpdate(
        bot_token='secret', app_id='app-1', tenant_id='tenant-1', answer_others=True), ctx)
    assert saved['app_id'] == 'app-1' and saved['tenant_id'] == 'tenant-1' and saved['answer_others'] is True
    assert saved['webhook_path'].startswith('/api/v1/channels/teams/') and saved['link_code']
    assert 'secret' not in json.dumps(saved)
    with pytest.raises(HTTPException) as err:
        await routes.update_channel('telegram', routes.ChannelUpdate(answer_others=True), ctx)
    assert err.value.status_code == 400


# --- meeting notes from recordings ----------------------------------------------


def _wav(seconds: int, rate: int = 8000) -> bytes:
    import struct

    pcm = b'\x00\x00' * (rate * seconds)
    return (b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVE' + b'fmt '
            + struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16) + b'data' + struct.pack('<I', len(pcm)) + pcm)


class _NotesLLM:
    def __init__(self):
        self.calls = []

    async def __call__(self, prompt, stream=False):
        self.calls.append(prompt)
        part = 'transcript_part' in prompt[1]['content']
        return SimpleNamespace(error=None, llm_response='- point' if part else '## Summary\nWe agreed to ship.')


@pytest.fixture
async def meetings(db, tmp_path, monkeypatch):
    from cognitrix.meetings import notes
    from cognitrix.models.voice_settings import VoiceSettings

    for model in (notes.MeetingNote, VoiceSettings):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()
    heard = []

    async def fake_transcribe(audio, *, mime, provider, model, api_key, language=None):
        heard.append((len(audio), mime, provider))
        return f'Ama said we ship on Friday and Kofi will write the release notes, part {len(heard)}.'

    async def voice(_user):
        return 'groq', 'whisper', 'key'

    monkeypatch.setattr(notes, 'transcribe', fake_transcribe)
    monkeypatch.setattr(notes, '_voice', voice)
    store = tmp_path / 'recordings'
    store.mkdir()
    monkeypatch.setattr(notes, 'storage_dir', lambda: store)
    llm = _NotesLLM()

    async def agent_llm(_user, agent_id):
        return SimpleNamespace(id=agent_id or 'a1', llm=llm)

    monkeypatch.setattr(notes, '_agent_llm', agent_llm)
    return SimpleNamespace(notes=notes, heard=heard, llm=llm, tmp=store)


async def test_a_recording_becomes_a_transcript_and_notes_and_is_then_deleted(meetings, monkeypatch):
    notes = meetings.notes
    monkeypatch.setattr(notes, 'ffmpeg_path', lambda: '')
    monkeypatch.setattr(notes, 'PIECE_SECONDS', 60)
    source = meetings.tmp / 'standup.wav'
    source.write_bytes(_wav(150))
    row = notes.MeetingNote(user_id='u1', title='Standup', filename='standup.wav')
    await row.save()

    await notes.process(str(row.id), source)
    done = await notes.MeetingNote.get(row.id)
    assert done.status == notes.DONE and done.error == '' and done.progress == ''
    assert len(meetings.heard) == 3 and all(mime == 'audio/wav' for _, mime, _ in meetings.heard)
    assert done.transcript.count('we ship on Friday') == 3 and round(done.seconds) == 150
    assert done.notes.startswith('## Summary') and done.agent_id == 'a1'
    assert not source.exists()
    system, user = meetings.llm.calls[-1]
    assert 'Never invent' in system['content'] and 'Meeting title: Standup' in user['content']


async def test_long_transcripts_are_read_in_parts_and_failures_are_explained(meetings, monkeypatch):
    notes = meetings.notes
    monkeypatch.setattr(notes, 'NOTES_CHUNK_CHARS', 200)
    text = await notes.write_notes(meetings.llm, 'We talked about the launch. ' * 30)
    assert text.startswith('## Summary') and len(meetings.llm.calls) > 2
    assert '<parts>' in meetings.llm.calls[-1][1]['content']
    with pytest.raises(notes.MeetingError):
        await notes.write_notes(meetings.llm, 'hm')

    # No ffmpeg and a video file: the person is told what to do, and the file is removed.
    monkeypatch.setattr(notes, 'ffmpeg_path', lambda: '')
    source = meetings.tmp / 'call.mp4'
    source.write_bytes(b'\x00' * 2048)
    row = notes.MeetingNote(user_id='u1', title='Call', filename='call.mp4')
    await row.save()
    await notes.process(str(row.id), source)
    failed = await notes.MeetingNote.get(row.id)
    assert failed.status == notes.FAILED and 'ffmpeg' in failed.error and not source.exists()


async def test_ffmpeg_turns_other_formats_into_pieces(meetings, tmp_path):
    import shutil

    notes = meetings.notes
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is not installed')
    source = tmp_path / 'talk.wav'
    source.write_bytes(_wav(3, rate=44100))
    out = tmp_path / 'out'
    out.mkdir()
    pieces = await notes.prepare(source, out)
    assert len(pieces) == 1 and pieces[0][1] == 'audio/wav'
    info = notes.parse_wav(pieces[0][0])
    assert info.sample_rate == 16000 and info.channels == 1 and round(info.seconds) == 3
    bad = tmp_path / 'notes.mp3'
    bad.write_bytes(b'this is not audio at all')
    with pytest.raises(notes.MeetingError):
        await notes.prepare(bad, out)


async def test_meetings_belong_to_the_person_who_uploaded_them(meetings, monkeypatch):
    import io

    from starlette.datastructures import UploadFile

    from cognitrix.api.routes import meetings as routes

    notes = meetings.notes
    monkeypatch.setattr(notes, 'ffmpeg_path', lambda: '')
    upload = UploadFile(io.BytesIO(_wav(5)), filename='../../etc/weekly sync.wav')
    made = await routes.create_meeting(file=upload, title='', agent_id='', language='', ctx=_ctx('u1'))
    assert made['title'] == 'weekly sync' and made['filename'] == 'weekly sync.wav'
    await notes._jobs[made['id']]
    mine = await routes.get_meeting(made['id'], _ctx('u1'))
    assert mine['status'] == 'done' and mine['notes'] and mine['transcript']
    assert list(meetings.tmp.glob('*.wav')) == []  # stored under our own name, then removed

    listed = await routes.list_meetings(_ctx('u1'))
    assert [m['id'] for m in listed['meetings']] == [made['id']] and 'transcript' not in listed['meetings'][0]
    assert (await routes.list_meetings(_ctx('u2')))['meetings'] == []
    for call in (routes.get_meeting(made['id'], _ctx('u2')), routes.delete_meeting(made['id'], _ctx('u2')),
                 routes.write_again(made['id'], routes.NotesAgain(), _ctx('u2', role='admin'))):
        with pytest.raises(HTTPException) as err:
            await call
        assert err.value.status_code == 404

    with pytest.raises(HTTPException) as err:
        await routes.create_meeting(file=UploadFile(io.BytesIO(b'x'), filename='virus.exe'),
                                    title='', agent_id='', language='', ctx=_ctx('u1'))
    assert err.value.status_code == 415

    again = await routes.write_again(made['id'], routes.NotesAgain(), _ctx('u1'))
    assert again['status'] == 'writing'
    await notes._jobs[made['id']]
    assert (await routes.delete_meeting(made['id'], _ctx('u1'))) == {'status': 'deleted'}


async def test_meetings_cut_off_by_a_restart_are_marked_failed(meetings):
    notes = meetings.notes
    row = notes.MeetingNote(user_id='u1', title='Half', status=notes.TRANSCRIBING)
    await row.save()
    (meetings.tmp / 'leftover.mp4').write_bytes(b'x')
    await notes.fail_interrupted()
    assert (await notes.MeetingNote.get(row.id)).status == notes.FAILED
    assert not (meetings.tmp / 'leftover.mp4').exists()


# --- WhatsApp -------------------------------------------------------------------


def _whatsapp_rig():
    import hashlib
    import hmac

    from cognitrix.channels.whatsapp import WhatsAppTransport

    transport = WhatsAppTransport('token', '1055', 'app-secret')
    sent = []

    async def call(method, path, body=None):
        sent.append((method, path, body))
        return {'messages': [{'id': f'wamid.{len(sent)}'}]}

    transport._call = call

    def signed(payload):
        raw = json.dumps(payload).encode()
        return 'sha256=' + hmac.new(b'app-secret', raw, hashlib.sha256).hexdigest(), raw

    def webhook(*messages, phone_id='1055'):
        return {'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': phone_id},
            'contacts': [{'wa_id': '23350', 'profile': {'name': 'Ama'}}],
            'messages': list(messages),
        }}]}]}

    return transport, sent, signed, webhook


async def test_whatsapp_reads_signed_messages_once_and_ignores_the_rest():
    from cognitrix.channels.base import ButtonPress, Handlers, Incoming
    from cognitrix.channels.whatsapp import WhatsAppAuthError

    transport, sent, signed, webhook = _whatsapp_rig()
    got = []

    async def collect(item):
        got.append(item)

    transport._handlers = Handlers(on_message=collect, on_button=collect)
    text = {'from': '23350', 'id': 'm1', 'type': 'text', 'text': {'body': ' hello '}}
    await transport.receive(*signed(webhook(text)))
    await transport.receive(*signed(webhook(text)))  # Meta's retry of the same message
    press = {'from': '23350', 'id': 'm2', 'type': 'interactive', 'context': {'id': 'wamid.7'},
             'interactive': {'type': 'button_reply', 'button_reply': {'id': 'ca:r1:1', 'title': 'Approve'}}}
    await transport.receive(*signed(webhook(press)))
    await transport.receive(*signed(webhook({'from': '999', 'id': 'm3', 'type': 'text', 'text': {'body': 'x'}},
                                            phone_id='another-number')))
    assert got == [Incoming('23350', '23350', 'Ama', 'hello'), ButtonPress('23350', '23350', 'ca:r1:1', 'wamid.7')]

    await transport.receive(*signed(webhook({'from': '23350', 'id': 'm4', 'type': 'image'})))
    assert len(got) == 2 and 'only read text' in sent[-1][2]['text']['body']

    signature, raw = signed(webhook({'from': '23350', 'id': 'm5', 'type': 'text', 'text': {'body': 'forged'}}))
    for bad in ('', 'sha256=' + '0' * 64, signature.replace('sha256', 'sha1')):
        with pytest.raises(WhatsAppAuthError):
            await transport.receive(bad, raw)
    with pytest.raises(WhatsAppAuthError):
        await transport.receive(signature, raw + b' ')
    assert len(got) == 2
    await transport.close()


async def test_whatsapp_sends_buttons_lists_and_its_own_formatting():
    from cognitrix.channels.base import Button
    from cognitrix.channels.whatsapp import to_whatsapp

    transport, sent, _signed, _webhook = _whatsapp_rig()
    assert to_whatsapp('## Plan\n**Ship** on [Friday](https://x.test/a)\n* one\n`**raw**`') == (
        '*Plan*\n*Ship* on Friday (https://x.test/a)\n- one\n`**raw**`')
    assert await transport.send('23350', '**Run** it?', [Button('Approve', 'ca:1:1'), Button('Deny', 'ca:1:0')]) == 'wamid.1'
    body = sent[-1][2]
    assert sent[-1][1] == '/1055/messages' and body['to'] == '23350'
    assert body['interactive']['type'] == 'button' and body['interactive']['body']['text'] == '*Run* it?'
    assert [b['reply']['id'] for b in body['interactive']['action']['buttons']] == ['ca:1:1', 'ca:1:0']
    await transport.send('23350', 'Pick', [Button(f'Option number {i} with a long label', f'q:1:{i}') for i in range(5)])
    rows = sent[-1][2]['interactive']['action']['sections'][0]['rows']
    assert sent[-1][2]['interactive']['type'] == 'list' and len(rows) == 5 and all(len(r['title']) <= 24 for r in rows)
    await transport.close()


async def test_whatsapp_settings_webhook_handshake_and_closed_window(db, monkeypatch):
    from cognitrix.api.routes import channels as routes
    from cognitrix.channels import service as channel_service
    from cognitrix.channels import whatsapp
    from cognitrix.channels.base import BotInfo
    from cognitrix.channels.service import ChannelService
    from cognitrix.models.chat_channel import ChannelDelivery, ChatChannel
    from cognitrix.tasks.automation import AgentNotification, create_notification

    class Probe:
        async def check(self):
            return BotInfo(name='+233 50 000 0000')

        async def close(self):
            pass

    monkeypatch.setattr(routes.channel_service, 'make_transport', lambda _row: Probe())
    ctx = _ctx('u1')
    with pytest.raises(HTTPException) as err:
        await routes.update_channel('whatsapp', routes.ChannelUpdate(bot_token='tok', app_id='1055'), ctx)
    assert err.value.status_code == 400
    saved = await routes.update_channel('whatsapp', routes.ChannelUpdate(
        bot_token='tok', app_id='1055', app_secret='app-secret'), ctx)
    row = await ChatChannel.find_one({'user_id': 'u1', 'kind': 'whatsapp'})
    assert saved['app_id'] == '1055' and saved['needs_public_url'] and 'app-secret' not in json.dumps(saved)
    assert saved['webhook_path'] == f'/api/v1/channels/whatsapp/{row.id}/webhook'
    assert saved['link_url'] == f'https://wa.me/233500000000?text={saved["link_code"]}'

    def req(**query):
        return SimpleNamespace(query_params=query)

    ok = await routes.whatsapp_verify(str(row.id), req(**{
        'hub.mode': 'subscribe', 'hub.verify_token': saved['verify_token'], 'hub.challenge': '8675309'}))
    assert ok.body == b'8675309'
    for query in ({'hub.mode': 'subscribe', 'hub.verify_token': 'guess', 'hub.challenge': '1'},
                  {'hub.mode': 'subscribe', 'hub.verify_token': saved['verify_token'], 'hub.challenge': '<script>'}):
        with pytest.raises(HTTPException) as err:
            await routes.whatsapp_verify(str(row.id), req(**query))
        assert err.value.status_code == 403

    # Outside WhatsApp's 24 hour window an alert is refused: it stays in the Inbox and isn't retried.
    create = getattr(AgentNotification, '_create_table_async', None) or AgentNotification.create_table
    await create()
    from cognitrix.tasks.approvals import TaskApproval

    await (getattr(TaskApproval, '_create_table_async', None) or TaskApproval.create_table)()
    await ChatChannel.update_one({'id': row.id}, {'chat_id': '23350', 'peer_id': '23350'})

    class Closed(_ThreadTransport):
        kind = 'whatsapp'

        async def send(self, chat_id, text, buttons=None, thread_id=''):
            self.sent.append(text)
            raise whatsapp.WhatsAppWindowClosed('24 hours')

    transport = Closed()
    service = ChannelService(transport_factory=lambda _row: transport)
    service._transports[row.id] = transport
    service._connected.add(row.id)
    await create_notification('u1', 'Build finished', 'All green')
    assert await service.deliver_once() == 1 and await service.deliver_once() == 0
    assert len(transport.sent) == 1 and len(await ChannelDelivery.find({'channel_id': row.id})) == 1
    assert channel_service.WEBHOOK_KINDS == ('teams', 'whatsapp')
