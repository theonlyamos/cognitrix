"""Agents in live calls: the desktop's sound in the sandbox, getting through a
join screen, listening, answering when named, and leaving with notes."""

import asyncio
import base64
import io
import math
import struct
import wave
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.common.security import AuthContext
from cognitrix.computer import Element


def _ctx(user='u1'):
    return AuthContext(user=SimpleNamespace(id=user, role='member', email=f'{user}@x.test'), api_key=None)


# --- sound in the sandbox ------------------------------------------------------


def _frame(loud: bool) -> bytes:
    from cognitrix.computer.sandbox import audio

    n = audio.FRAME_BYTES // 2
    level = 3000 if loud else 0
    return struct.pack(f'<{n}h', *(int(level * math.sin(i / 3)) for i in range(n)))


def test_speech_is_cut_into_pieces_at_pauses_and_silence_is_dropped():
    from cognitrix.computer.sandbox import audio

    seg = audio.Segmenter()
    out = []
    # 2 s of silence, 1.5 s of talk, 1 s pause, a click, 2 s silence
    for loud in [False] * 20 + [True] * 15 + [False] * 10 + [True] + [False] * 20:
        piece = seg.push(_frame(loud))
        if piece:
            out.append(piece)
    assert seg.flush() is None
    assert len(out) == 1  # the click alone is too short to be speech
    seconds = len(out[0]) / (audio.RATE * 2)
    assert 2.0 <= seconds <= 3.0  # the talk, a little lead-in and the pause

    # A long talk without pauses is cut at MAX_PIECE_S.
    seg = audio.Segmenter()
    cuts = [p for p in (seg.push(_frame(True)) for _ in range(int(audio.MAX_PIECE_S / audio.FRAME_S) + 5)) if p]
    assert len(cuts) == 1
    with wave.open(io.BytesIO(audio.wav_bytes(cuts[0]))) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1


def test_read_pieces_are_handed_over_and_dropped():
    from cognitrix.computer.sandbox import audio

    rec = audio.Recorder('x.monitor')
    for _ in range(3):
        rec._add(_frame(True) * 10)
    assert [p['seq'] for p in rec.pieces_after(0)] == [1, 2, 3]
    assert [p['seq'] for p in rec.pieces_after(2)] == [3]
    assert len(rec._pieces) == 1
    assert base64.b64decode(rec.pieces_after(2)[0]['audio'])[:4] == b'RIFF'


def test_each_desktop_gets_its_own_speaker_and_microphone(monkeypatch):
    from cognitrix.computer.sandbox import audio

    loaded = []
    monkeypatch.setattr(audio, 'pulse_ready', lambda: True)
    monkeypatch.setattr(audio, '_pactl', lambda *a: loaded.append(a) or str(len(loaded)))
    env = audio.DesktopAudio('agent-7').setup()
    assert env == {'PULSE_SINK': 'cx_agent_7_out', 'PULSE_SOURCE': 'cx_agent_7_mic'}
    assert any('master=cx_agent_7_feed.monitor' in a for call in loaded for a in call)
    # No PulseAudio in an old image: the desktop still starts, without sound.
    monkeypatch.setattr(audio, 'pulse_ready', lambda: False)
    plain = audio.DesktopAudio('b')
    assert plain.setup() == {} and plain.start()['error'] == 'no_audio'


# --- the join screen --------------------------------------------------------------


def _el(role, name='', value=None):
    return Element(role=role, name=name, value=value, rect=(100, 100, 200, 140))


class FakeDriver:
    """A call page that changes as the agent acts on it."""

    def __init__(self, lobby_looks=1):
        self.state = 'prejoin'
        self.name = ''
        self.lobby_looks = lobby_looks
        self.chat_open = False
        self.typed = []
        self.clicked = []
        self.snapshots = 0
        self.box = None
        self.desktop = 'a1'

    def open(self, target, args=None, timeout_s=12.0):
        assert target == 'chromium' and args.startswith('--new-window https://')
        return SimpleNamespace(id=7, app='chromium', title='Meet')

    def list_windows(self, query=None):
        return []

    def _elements(self):
        if self.state == 'prejoin':
            return [_el('entry', 'Your name', self.name), _el('push button', 'Ask to join'),
                    _el('push button', 'Present')]
        if self.state == 'lobby':
            return [_el('paragraph', '', 'Asking to be let in…'), _el('push button', 'Cancel')]
        if self.state == 'ended':
            return [_el('heading', 'You left the meeting'), _el('push button', 'Rejoin')]
        items = [_el('push button', 'Leave call'), _el('toggle button', 'Chat with everyone')]
        if self.chat_open:
            items.append(_el('entry', 'Send a message'))
        return items

    def look(self, window_id, query=None, limit=60, viewer=None):
        self.snapshots += 1
        if self.state == 'lobby':
            self.lobby_looks -= 1
            if self.lobby_looks < 0:
                self.state = 'live'
        snap = SimpleNamespace(id=f's{self.snapshots}', window_id=window_id, elements=self._elements(),
                               origin=(0, 0), size=(1280, 800), scale=1.0)
        return None, snap, [], ''

    def act_on_element(self, window_id, index, action='click', snapshot_id=None, viewer=None, use_screen=False):
        name = self._elements()[index].name
        self.clicked.append(name)
        if name == 'Ask to join':
            self.state = 'lobby'
        elif name == 'Chat with everyone':
            self.chat_open = True
        elif name == 'Leave call':
            self.state = 'ended'

    def type_text(self, window_id, text, x=None, y=None, viewer=None, use_screen=False):
        assert use_screen and x is not None
        if self.state == 'prejoin':
            self.name = text
        else:
            self.typed.append(text)

    def press_key(self, window_id, keys, use_screen=False):
        self.clicked.append(keys)


def test_join_types_the_name_asks_to_join_and_waits_in_the_lobby():
    from cognitrix.meetings import live

    driver = FakeDriver()
    screen = live.Screen(driver, 'meeting:1')
    screen.open('https://meet.google.com/abc-defg-hij')
    typed: set[int] = set()
    seen = [live.join_step(screen, 'Ada (notes)', typed) for _ in range(4)]
    assert seen == ['working', 'working', 'lobby', 'live']
    assert driver.name == 'Ada (notes)' and driver.clicked == ['Ask to join']
    assert live.post_chat(screen, 'Hello\nall') and driver.typed == ['Hello all\n']


def test_refusals_and_links():
    from cognitrix.meetings import live
    from cognitrix.meetings.notes import MeetingError

    view = live.View(1, 's', [_el('heading', "You can't join this video call")])
    assert view.says(live.REFUSED)
    assert live.check_link('https://us02web.zoom.us/j/8812345678?pwd=abc') == (
        'https://us02web.zoom.us/wc/join/8812345678?pwd=abc', 'zoom')
    assert live.check_link(' https://teams.microsoft.com/l/meetup-join/x ')[1] == 'teams'
    for bad in ('http://meet.google.com/x', 'meet.google.com/x', 'javascript:alert(1)'):
        with pytest.raises(MeetingError):
            live.check_link(bad)
    assert live.addressed('Okay ada, what did we decide?', live.call_names('Ada Notes', 'Ada'))
    assert not live.addressed('the adapter is broken', ['Ada'])


# --- a whole call ----------------------------------------------------------------


class FakeBox:
    def __init__(self):
        self.calls = []
        self.served = False
        self.said = []

    def call(self, method, path, body=None, *, params=None, timeout=None, content=None):
        self.calls.append((method, path))
        if path.endswith('/audio') and method == 'GET':
            if self.served:
                return {'ok': True, 'listening': True, 'pieces': []}
            self.served = True
            wav = base64.b64encode(b'RIFF....WAVE').decode()
            return {'ok': True, 'listening': True, 'pieces': [{'seq': 1, 'audio': wav}, {'seq': 2, 'audio': wav}]}
        if path.endswith('/say'):
            self.said.append(body)
        return {'ok': True}


class AnswerLLM:
    def __init__(self):
        self.calls = []

    async def __call__(self, prompt, stream=False):
        self.calls.append(prompt)
        if '<transcript>' in prompt[1]['content'] and 'Never agree' in prompt[0]['content']:
            return SimpleNamespace(error=None, llm_response='Friday, as Kofi said.')
        return SimpleNamespace(error=None, llm_response='## Summary\nWe ship Friday.')


@pytest.fixture
async def call(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.meetings import live, notes
    from cognitrix.models.voice_settings import VoiceSettings

    db_file = str(tmp_path / 'm.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (notes.MeetingNote, VoiceSettings):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()

    said = iter(['Good morning everyone, we ship on Friday and Kofi writes the notes.',
                 "Ada, what's the launch date again?"])

    async def fake_transcribe(audio, *, mime, provider, model, api_key, language=None):
        return next(said, '')

    async def voice(_user):
        return 'groq', 'whisper', 'key'

    llm = AnswerLLM()

    async def agent_llm(_user, agent_id):
        return SimpleNamespace(id=agent_id or 'a1', name='Ada', llm=llm)

    driver, box = FakeDriver(lobby_looks=0), FakeBox()
    driver.box = box

    class Desk(live._Desk):
        def __init__(self, user_id, agent_id, agent_name, meeting_id):
            from cognitrix.computer import Actor

            self.actor = Actor(agent_id=agent_id, agent=agent_name, user_id=user_id)
            self.driver, self.box, self.desktop = driver, box, 'a1'
            self.screen = live.Screen(driver, f'meeting:{meeting_id}')

    alerts = []

    async def alert(row, title, message):
        alerts.append(title)

    monkeypatch.setattr(notes, 'transcribe', fake_transcribe)
    monkeypatch.setattr(notes, '_voice', voice)
    monkeypatch.setattr(notes, '_agent_llm', agent_llm)
    monkeypatch.setattr(live, '_Desk', Desk)
    monkeypatch.setattr(live, '_alert', alert)
    monkeypatch.setattr(live, 'JOIN_POLL_S', 0)
    monkeypatch.setattr(live, 'POLL_S', 0.05)
    monkeypatch.setattr(live, 'UI_WAIT_S', 0)
    monkeypatch.setattr(live, 'ANSWER_GAP_S', 0)
    import cognitrix.computer.sandbox.client as client
    monkeypatch.setattr(client, 'sandbox_mode', lambda: 'docker')
    return SimpleNamespace(live=live, notes=notes, driver=driver, box=box, llm=llm, alerts=alerts)


async def test_an_agent_joins_listens_answers_when_named_and_leaves_with_notes(call):
    from cognitrix.api.routes import meetings as routes

    made = await routes.join_live(routes.LiveJoin(url='https://meet.google.com/abc-defg-hij', reply_mode='chat'),
                                  _ctx('u1'))
    assert made['kind'] == 'live' and made['platform'] == 'meet' and made['bot_name'] == 'Ada'
    meeting_id = made['id']
    for _ in range(200):
        row = await call.notes.MeetingNote.get(meeting_id)
        if row.answers:
            break
        await asyncio.sleep(0.02)
    assert row.status == call.live.LIVE and row.answers == 1
    assert "Hi, I'm Ada" in call.driver.typed[0] and call.driver.typed[1] == 'Friday, as Kofi said.\n'
    assert 'we ship on Friday' in row.transcript and 'Ada (chat): Friday, as Kofi said.' in row.transcript

    # Someone else's meeting can't be left, and an agent can't be in two calls.
    with pytest.raises(HTTPException) as err:
        await routes.leave_live(meeting_id, _ctx('u2'))
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        await routes.join_live(routes.LiveJoin(url='https://meet.google.com/other'), _ctx('u1'))
    assert err.value.status_code == 409

    await routes.say_live(meeting_id, routes.SayInCall(text='One moment please'), _ctx('u1'))
    await asyncio.sleep(0.3)
    assert 'One moment please\n' in call.driver.typed
    await routes.leave_live(meeting_id, _ctx('u1'))
    for _ in range(200):
        row = await call.notes.MeetingNote.get(meeting_id)
        if row.status == call.notes.DONE:
            break
        await asyncio.sleep(0.02)
    assert row.status == call.notes.DONE and row.notes.startswith('## Summary')
    assert 'Leave call' in call.driver.clicked and ('POST', '/desktops/a1/audio/stop') in call.box.calls
    assert call.alerts == ['Notes from Google Meet call']
    assert not call.live.running(meeting_id)


async def test_calls_need_the_sandboxed_computer(call, monkeypatch):
    import cognitrix.computer.sandbox.client as client
    from cognitrix.api.routes import meetings as routes

    monkeypatch.setattr(client, 'sandbox_mode', lambda: None)
    with pytest.raises(HTTPException) as err:
        await routes.join_live(routes.LiveJoin(url='https://meet.google.com/abc'), _ctx('u1'))
    assert err.value.status_code == 409 and "Agents' computer" in err.value.detail


async def test_a_call_cut_off_by_a_restart_keeps_what_was_heard(call):
    row = call.notes.MeetingNote(user_id='u1', kind='live', status=call.live.LIVE, transcript='[00:01] hi')
    await row.save()
    await call.live.fail_interrupted()
    again = await call.notes.MeetingNote.get(row.id)
    assert again.status == call.notes.FAILED and again.transcript == '[00:01] hi'


def test_joining_a_call_asks_first_and_following_it_does_not():
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel

    d = DestructiveOpDetector()
    risk = d.analyze('Join Meeting', {'url': 'https://meet.google.com/x', 'reply_mode': 'voice'})
    assert risk.risk_level == RiskLevel.MEDIUM and 'out loud' in risk.details
    for name in ('Meeting Transcript', 'Leave Meeting', 'Say In Meeting'):
        assert d.analyze(name, {}).risk_level == RiskLevel.LOW
