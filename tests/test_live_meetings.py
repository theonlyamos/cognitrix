"""Agents in live calls: the desktop's sound in the sandbox, getting through a
join screen, listening, answering when named, and leaving with notes."""

import asyncio
import base64
import io
import math
import struct
import threading
import wave
from types import SimpleNamespace
from urllib.parse import urlsplit

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


def test_stopping_returns_only_speech_newer_than_the_cursor(monkeypatch):
    from cognitrix.computer.sandbox import audio

    desktop = audio.DesktopAudio('agent')
    rec = desktop.recorder = audio.Recorder('test.monitor')
    rec._add(_frame(True))
    # The recording thread flushes the pending utterance as it exits.
    monkeypatch.setattr(rec, 'stop', lambda: rec._add(_frame(True) * 6))
    pieces = desktop.stop(after=1)['pieces']
    assert [piece['seq'] for piece in pieces] == [2]
    assert pieces[0]['seconds'] == 0.6
    assert desktop.read(1)['pieces'] == []


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
        self.url = ''
        self.window = 6

    def open(self, target, args=None, timeout_s=12.0):
        assert target == 'chromium' and args.startswith('--new-window https://')
        self.url = args.split(' ', 1)[1]
        self.window += 1  # every --new-window is another window
        return SimpleNamespace(id=self.window, app='chromium', title='Meet')

    def list_windows(self, query=None):
        return [SimpleNamespace(id=self.window, app='chromium', title='Meet')] if self.url else []

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
        elements = self._elements() + [_el('entry', 'Address and search bar', self.url)]
        snap = SimpleNamespace(id=f's{self.snapshots}', window_id=window_id, elements=elements,
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


@pytest.mark.parametrize(('elements', 'expected'), [
    ([_el('toggle button', 'Show everyone'), _el('push button', 'Chat with everyone')], 'live'),
    ([_el('push button', 'Turn off microphone'), _el('push button', 'Turn off camera')], 'working'),
    ([_el('push button', 'Leave')], 'working'),
    ([_el('link', 'Leave feedback')], 'working'),
    ([_el('heading', 'Someone in the meeting should let you in soon'), _el('push button', 'Leave')], 'lobby'),
    ([_el('heading', 'You left the meeting'), _el('push button', 'Rejoin')], 'ended'),
    ([_el('heading', 'Sign in to join this meeting'), _el('link', 'Sign in')], 'sign_in'),
    ([_el('entry', 'Address and search bar', 'accounts.google.com/v3/signin/identifier'),
      _el('entry', 'Email or phone'), _el('push button', 'Next')], 'sign_in'),
    # Signed in (Google's pages show the account) and still turned away: the host's choice.
    ([_el('heading', 'You can’t join this video call'), _el('push button', 'Google Account: Ada (ada@example.com)')],
     "refused: you can't join this video call"),
    # The same page in a signed-out browser: the person is asked to sign in.
    ([_el('heading', 'You can’t join this video call'), _el('push button', 'Return to home screen')], 'sign_in'),
])
def test_google_meet_admission_from_the_visible_controls(elements, expected):
    from cognitrix.meetings import live

    view = live.View(7, 's', elements)
    screen = SimpleNamespace(platform='meet', look=lambda: view)
    assert live.join_step(screen, 'Ada', set()) == expected


def test_optional_google_sign_in_allows_joining_as_a_guest():
    from cognitrix.meetings import live

    view = live.View(7, 's', [_el('link', 'Sign in to join'), _el('push button', 'Ask to join')])
    clicked = []
    screen = SimpleNamespace(platform='meet', look=lambda: view, click=lambda view, i: clicked.append(i))
    assert live.join_step(screen, 'Ada', set()) == 'working'
    assert clicked == [1]


@pytest.mark.parametrize('target_address', [
    'meet.google.com/abc-defg-hij?authuser=1',
    'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fmeet.google.com%2Fabc-defg-hij',
])
def test_join_uses_the_requested_window_when_chromium_restores_its_session(target_address):
    from cognitrix.meetings import live

    other = 'meet.google.com/xyz-abcd-xyz'
    windows = [SimpleNamespace(id=i, app='chromium', title='Meet - Chromium') for i in (3, 7, 9)]

    class RestoredBrowser(FakeDriver):
        def list_windows(self):
            return windows if self.url else windows[:1]

        def open(self, *args, **kwargs):
            super().open(*args, **kwargs)
            return windows[1]  # Chromium restored another room before opening the requested one.

        def look(self, window_id, *args, **kwargs):
            result = super().look(window_id, *args, **kwargs)
            address = {3: target_address, 7: other, 9: target_address}[window_id]
            result[1].elements = [_el('entry', 'Address and search bar', address)]
            return result

    screen = live.Screen(RestoredBrowser(), 'meeting:1')
    screen.open('https://meet.google.com/abc-defg-hij')
    assert screen.look().window_id == 9


def test_in_call_controls_take_precedence_over_chat_messages():
    from cognitrix.meetings import live

    view = live.View(7, 's', [_el('toggle button', 'Show everyone'), _el('push button', 'Raise hand'),
                              _el('paragraph', '', "Please wait in the lobby. Rejoin if you can't join this video call.")])
    screen = SimpleNamespace(platform='meet', look=lambda: view)
    assert live.join_step(screen, 'Ada', set()) == 'live'


def test_zoom_reuses_the_opened_window_and_connects_computer_audio():
    from cognitrix.meetings import live

    class Zoom(FakeDriver):
        def _elements(self):
            return [_el('push button', 'Leave'), _el('push button', 'Join Audio by Computer')]

    driver = Zoom()
    driver.url = 'https://zoom.us/'
    screen = live.Screen(driver, 'meeting:1')
    screen.open('https://zoom.us/wc/join/12345')
    assert live.join_step(screen, 'Ada', set()) == 'live'
    assert driver.clicked == ['Join Audio by Computer']


@pytest.mark.parametrize('other_url', ['https://docs.google.com/document/d/123', 'https://meet.google.com/xyz-abcd-xyz'])
def test_manual_tab_switch_does_not_click_or_close_the_unrelated_tab(other_url):
    from cognitrix.computer import Stale
    from cognitrix.meetings import live

    driver = FakeDriver()
    screen = live.Screen(driver, 'meeting:1')
    screen.open('https://meet.google.com/abc-defg-hij')
    screen.look()
    driver.url = other_url
    with pytest.raises(Stale):
        live.join_step(screen, 'Ada', set())
    screen.close()
    assert driver.clicked == [] and not driver.name


def test_meet_post_call_redirect_is_observed_and_left_alone():
    from cognitrix.meetings import live

    driver = FakeDriver()
    screen = live.Screen(driver, 'meeting:1')
    screen.open('https://meet.google.com/lookup/team')
    driver.url = 'https://meet.google.com/abc-defg-hij'
    assert screen.look().window_id == 7
    driver.url = 'https://meet.google.com/landing'
    driver.state = 'ended'
    assert live.join_step(screen, 'Ada', set()) == 'ended'
    screen.close()
    assert driver.clicked == []


@pytest.mark.parametrize(('address', 'expected'), [
    ('https://meet.google.com/abc-defg-hij', ['ctrl+w']),
    ('https://meet.google.com/xyz-abcd-xyz', []),
])
def test_stop_before_first_poll_closes_only_the_requested_meet(address, expected):
    from cognitrix.meetings import live

    driver = FakeDriver()
    screen = live.Screen(driver, 'meeting:1')
    screen.open('https://meet.google.com/abc-defg-hij')
    driver.url = address
    screen.close()
    assert driver.clicked == expected


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
    from cognitrix.meetings import extras, live, notes
    from cognitrix.models.voice_settings import VoiceSettings

    db_file = str(tmp_path / 'm.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (notes.MeetingNote, extras.MeetingSettings, VoiceSettings):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()

    said = iter(['Good morning everyone, we ship on Friday and Kofi writes the notes.',
                 "Ada, what's the launch date again?"])

    async def fake_transcribe(audio, *, mime, provider, model, api_key, language=None, **kwargs):
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

    async def alert(row, title, message, *, urgent=False):
        alerts.append(title)

    monkeypatch.setattr(notes, 'transcribe', fake_transcribe)
    monkeypatch.setattr(notes, 'storage_dir', lambda: tmp_path)
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
    return SimpleNamespace(live=live, notes=notes, extras=extras, driver=driver, box=box, llm=llm, alerts=alerts,
                           tmp=tmp_path)


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


async def test_first_utterance_finishing_on_leave_reaches_notes_without_a_reply(call, monkeypatch):
    from cognitrix.api.routes import meetings as routes

    polling = threading.Event()

    def audio(method, path, body=None, **kwargs):
        if method == 'GET':
            polling.set()  # A short utterance is still in progress.
            return {'ok': True, 'listening': True, 'pieces': []}
        if path.endswith('/stop') and body == {'after': 0}:
            return {'ok': True, 'pieces': [{'seq': 1, 'audio': base64.b64encode(b'goodbye').decode()}]}
        return {'ok': True}

    async def speech(clip, **kwargs):
        assert clip == b'goodbye'
        assert (await call.notes.MeetingNote.get(made['id'])).status == call.notes.TRANSCRIBING
        return 'Ada, can you confirm the Friday launch date?'

    monkeypatch.setattr(call.box, 'call', audio)
    monkeypatch.setattr(call.notes, 'transcribe', speech)
    made = await routes.join_live(routes.LiveJoin(url='https://meet.google.com/abc', announce=False), _ctx())
    assert await asyncio.to_thread(polling.wait, 3)
    task = call.live._live[made['id']].task
    await routes.leave_live(made['id'], _ctx())
    await asyncio.wait_for(task, 3)
    meeting = await call.notes.MeetingNote.get(made['id'])
    assert meeting.status == call.notes.DONE
    assert meeting.transcript.count('Ada, can you confirm the Friday launch date?') == 1
    assert 'Ada, can you confirm the Friday launch date?' in call.llm.calls[-1][1]['content']
    assert not meeting.answers


async def test_capture_resumes_from_the_first_piece_after_a_recorder_restart(call, monkeypatch):
    from cognitrix.api.routes import meetings as routes

    streams = [b'Before the microphone stopped.', b'After the microphone started again.']
    starts = 0
    recovered = threading.Event()

    def audio(method, path, body=None, *, params=None, **kwargs):
        nonlocal starts
        if path.endswith('/start'):
            starts += 1
        if method == 'GET':
            stopped = starts == 1
            # Each new Recorder's sequence starts at 1.
            piece = {'seq': 1, 'audio': base64.b64encode(streams[starts - 1]).decode()}
            if starts == 2:
                recovered.set()
            return {'ok': True, 'listening': not stopped, 'error': 'stopped' if stopped else '',
                    'pieces': [piece] if params['after'] == 0 else []}
        return {'ok': True}

    async def transcribe(clip, **kwargs):
        return clip.decode()

    monkeypatch.setattr(call.box, 'call', audio)
    monkeypatch.setattr(call.notes, 'transcribe', transcribe)
    made = await routes.join_live(routes.LiveJoin(url='https://meet.google.com/abc', announce=False), _ctx())
    assert await asyncio.to_thread(recovered.wait, 3)
    task = call.live._live[made['id']].task
    await routes.leave_live(made['id'], _ctx())
    await asyncio.wait_for(task, 3)
    meeting = await call.notes.MeetingNote.get(made['id'])
    assert all(meeting.transcript.count(s.decode()) == 1 for s in streams)


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


async def test_stopping_in_the_lobby_closes_the_page():
    from cognitrix.meetings import live

    calls = []

    class Desk:
        screen = SimpleNamespace(close=lambda: calls.append('close'))

        async def audio(self, method, action=''):
            calls.append(f'audio {action}')
            return {'ok': True}

        async def do(self, fn, *args):
            calls.append(getattr(fn, '__name__', 'fn'))
            return fn(*args)

    await live._cleanup(Desk(), joined=False)
    assert calls[-1] == 'close'


async def test_manual_google_sign_in_resumes_the_same_meeting_after_hand_back(call, monkeypatch):
    from cognitrix.computer import HumanControl

    row = call.notes.MeetingNote(user_id='u1', kind='live', platform='meet', bot_name='Ada',
                                 url='https://meet.google.com/abc-defg-hij', status='joining')
    await row.save()
    desk = call.live._Desk('u1', 'a1', 'Ada', row.id)
    original_look = call.driver.look
    phase = 'sign in'

    def look(*args, **kwargs):
        if phase == 'held':
            raise HumanControl('The person has control.')
        image, snap, shown, note = original_look(*args, **kwargs)
        if phase == 'sign in':
            snap.elements = [_el('heading', 'Sign in to join this meeting')]
        return image, snap, shown, note

    async def handoff(_seconds):
        nonlocal phase
        current = await call.notes.MeetingNote.get(row.id)
        assert current.status == 'joining'
        assert not call.box.calls
        if phase == 'sign in':
            assert 'sign in' in current.progress.lower()
            phase = 'held'
        else:
            assert 'hand back' in current.progress.lower()
            phase = call.driver.state = 'live'
            call.driver.url = row.url  # signed in, Google sends the browser back to the call

    monkeypatch.setattr(call.driver, 'look', look)
    monkeypatch.setattr(call.live, 'asyncio', SimpleNamespace(sleep=handoff, to_thread=asyncio.to_thread))
    await call.live._join(call.live.Live(row.id), desk, row)
    assert phase == 'live'


async def test_lost_lobby_controls_still_time_out(call, monkeypatch):
    row = call.notes.MeetingNote(user_id='u1', kind='live', platform='meet',
                                 url='https://meet.google.com/abc-defg-hij')
    await row.save()
    desk = call.live._Desk('u1', 'a1', 'Ada', row.id)
    views = iter([[_el('paragraph', 'Waiting for the host')], [], [_el('button', 'Leave call')]])
    monkeypatch.setattr(call.driver, '_elements', lambda: next(views))
    clock = 0.0

    async def advance(_seconds):
        nonlocal clock
        clock += call.live.LOBBY_WAIT_S + 1

    monkeypatch.setattr(call.live, 'time', SimpleNamespace(monotonic=lambda: clock))
    monkeypatch.setattr(call.live, 'asyncio', SimpleNamespace(sleep=advance, to_thread=asyncio.to_thread))
    with pytest.raises(call.notes.MeetingError):
        await call.live._join(call.live.Live(row.id), desk, row)


# --- what went wrong in a real Google Meet test (Nov 28) ------------------------


class _Atspi:
    """Just enough of AT-SPI for LinuxBackend._frame: one Chromium process
    with its frames, in the order Chromium made them."""

    def __init__(self, pid, names):
        self.desktop = 'desktop'
        self.app = SimpleNamespace(ptr='app')
        self.frames = [SimpleNamespace(ptr=f'f{i}', name=n) for i, n in enumerate(names)]
        self.lib = SimpleNamespace(atspi_accessible_get_process_id=lambda ptr, _err: pid)

    def pump(self):
        pass

    def children(self, acc, limit):
        return [self.app] if acc == self.desktop else self.frames

    def role(self, acc):
        return 'frame'

    def name(self, acc):
        return acc.name

    def states(self, acc):
        from cognitrix.computer import linux

        return {linux._ST_SHOWING}

    def extents(self, acc):
        return (0, 0, 0, 0)


def _backend(names, windows):
    from cognitrix.computer import WindowInfo, linux

    backend = object.__new__(linux.LinuxBackend)
    backend.atspi = _Atspi(42, names)
    infos = {wid: WindowInfo(id=wid, title=title, app='chromium', pid=42, minimized=False, rect=(0, 0, 1280, 800))
             for wid, title in windows}
    backend._client_windows = lambda: list(infos)
    backend._info = infos.get
    backend.foreground = lambda: 0
    return backend, infos


def test_a_browser_window_using_the_microphone_can_still_be_read():
    """Chromium names a window that records sound "Meet - Microphone recording
    - Chromium" for screen readers while its title stays "Meet - Chromium".
    The driver lost the window (no controls at all), so the meeting engine
    never saw Meet's join screen or the call: it sat on "Joining"."""
    from cognitrix.computer.linux import title_key

    assert title_key('Meet - Microphone recording - Chromium') == title_key('Meet - Chromium')
    assert title_key('Meet - abc-defg-hij - Camera or microphone recording - Audio playing - Chromium') == \
        'meet - abc-defg-hij - chromium'
    assert title_key('Recording studio - Chromium') == 'recording studio - chromium'

    backend, infos = _backend(['Meet - Microphone recording - Chromium', '', ''], [(7, 'Meet - Chromium')])
    assert backend._frame(infos[7]).name == 'Meet - Microphone recording - Chromium'


def test_two_windows_with_the_same_title_each_read_their_own_page():
    """An earlier Meet window (or the one the person signed in from) has the
    same title: each window gets its own frame, in the order they were made."""
    backend, infos = _backend(['Meet - Chromium', '', 'Meet - Microphone recording - Chromium', ''],
                              [(9, 'Meet - Chromium'), (12, 'Meet - Chromium')])
    assert backend._frame(infos[9]).ptr == 'f0'
    assert backend._frame(infos[12]).ptr == 'f2'
    backend, infos = _backend(['Mail - Chromium', 'Meet - Chromium'], [(9, 'Mail - Chromium'), (12, 'Meet - Chromium')])
    assert backend._frame(infos[12]).ptr == 'f1'


class _Page:
    """A screen that shows fixed controls and records what was pressed."""

    def __init__(self, *elements):
        self.elements = list(elements)
        self.clicked = []
        self.typed = []

    platform = 'meet'

    def look(self):
        from cognitrix.meetings import live

        return live.View(1, 's1', self.elements)

    def click(self, view, index):
        self.clicked.append(view.elements[index].name)

    def type_into(self, view, index, text, submit=False):
        self.typed.append(text)


def test_join_screens_seen_in_meet():
    from cognitrix.meetings import live

    # Signed out, guests allowed: it joins as a guest (Meet then asks its name).
    guest = _Page(_el('push button', 'Continue as guest'), _el('static', 'Or sign in to join'),
                  _el('push button', 'Sign in with Google'))
    assert live.join_step(guest, 'Ada', set()) == 'working' and guest.clicked == ['Continue as guest']
    # Google's sign-in page itself, also when the address bar can't be read: nothing is typed or pressed.
    google = _Page(_el('page tab', 'Sign in - Google Accounts - Memory usage - 88.4 MB'),
                   _el('entry', 'Email or phone'), _el('push button', 'Next'))
    assert live.join_step(google, 'Ada', set()) == 'sign_in' and not google.typed and not google.clicked
    # Meet's real join screen for a guest (Nov 28): its logo is a "Return to home screen" link, which is
    # not the call having ended, and its join button has a longer name and is greyed out until a name
    # is typed.
    ask = _el('push button', 'Ask to join without microphone & camera')
    ask.enabled = False
    prejoin = _Page(_el('link', 'Return to home screen'), _el('push button', 'Sign in'), _el('entry', 'Your name'),
                    ask, _el('dialog', 'Sign in with your Google account'), _el('push button', 'Got it'))
    typed: set[int] = set()
    assert not live.View(1, 's', prejoin.elements).in_call('meet')
    # The tip over the page is closed first, then the name is typed, then it asks to join.
    assert live.join_step(prejoin, 'Ada', typed) == 'working' and prejoin.clicked == ['Got it'] and not prejoin.typed
    del prejoin.elements[4:]
    assert live.join_step(prejoin, 'Ada', typed) == 'working' and prejoin.typed == ['Ada']
    ask.enabled = True
    assert live.join_step(prejoin, 'Ada', typed) == 'working'
    assert prejoin.clicked == ['Got it', 'Ask to join without microphone & camera']
    # Once the call is over, the same link does mean it ended.
    over = _Page(_el('link', 'Return to home screen'), _el('heading', 'You left the meeting'))
    assert live.join_step(over, 'Ada', set()) == 'ended'
    # Meet's current wording while the host decides.
    asking = _Page(_el('heading', 'Asking to join…'), _el('static', "You'll join the call when someone lets you in"))
    assert live.join_step(asking, 'Ada', set()) == 'lobby'
    # While the person signs in, a page it doesn't know is left alone.
    unknown = _Page(_el('entry', 'First name'), _el('push button', 'Join'))
    assert live.join_step(unknown, 'Ada', set(), {}, False) == 'working' and not unknown.typed and not unknown.clicked


def test_a_button_that_does_nothing_is_not_pressed_forever():
    from cognitrix.meetings import live

    page = _Page(_el('push button', 'Got it'), _el('push button', 'Join now'))
    clicks: dict[str, int] = {}
    for _ in range(live.MAX_SAME_CLICKS + 1):
        live.join_step(page, 'Ada', set(), clicks)
    assert page.clicked == ['Got it'] * live.MAX_SAME_CLICKS + ['Join now']


class _SignInBrowser:
    """A browser whose Meet turns a signed-out visitor away. Google's sign-in
    page signs the person in after a few looks and sends the window on to
    ``lands_on`` (the call by default)."""

    MEETING = 'https://meet.google.com/abc-defg-hij'

    def __init__(self, lands_on=None, held_looks=(), takes_over_at_google=False):
        self.takes_over_at_google = takes_over_at_google
        self.held_for = 0
        self.windows = {}  # id -> address
        self.signed_in = False
        self.in_call = False
        self.lands_on = lands_on or self.MEETING
        self.held_looks = set(held_looks)
        self.looks = 0
        self.sign_in_looks = 0
        self.opened, self.clicked = [], []

    def open(self, target, args=None, timeout_s=12.0):
        address = args.split(' ', 1)[1]
        self.opened.append(address)
        wid = 10 + len(self.opened)
        self.windows[wid] = address
        return SimpleNamespace(id=wid, app='chromium', title='Chromium')

    def list_windows(self, query=None):
        return [SimpleNamespace(id=wid, app='chromium', title='Chromium') for wid in self.windows]

    def _elements(self, window_id):
        address = self.windows[window_id]
        if urlsplit(address).hostname == 'accounts.google.com':
            self.sign_in_looks += 1
            if self.sign_in_looks > 3:  # the person has signed in
                self.signed_in = True
                address = self.windows[window_id] = self.lands_on
            else:
                return address, [_el('entry', 'Email or phone'), _el('push button', 'Next')]
        if address != self.MEETING:
            return address, [_el('heading', 'Welcome, Ada')]
        if not self.signed_in:
            return address, [_el('heading', "You can't join this video call")]
        return address, [_el('push button', 'Leave call' if self.in_call else 'Join now')]

    def look(self, window_id, query=None, limit=60, viewer=None):
        from cognitrix.computer import HumanControl

        self.looks += 1
        if self.takes_over_at_google and urlsplit(self.windows[window_id]).hostname == 'accounts.google.com':
            # The person takes over before the agent has read Google's page,
            # signs in there, and hands back a few looks later.
            self.takes_over_at_google, self.signed_in, self.held_for = False, True, 3
            self.windows[window_id] = self.lands_on
        if self.looks in self.held_looks or self.held_for > 0:
            self.held_for -= 1
            raise HumanControl('Ama has taken over this computer.')
        address, elements = self._elements(window_id)
        elements = elements + [_el('entry', 'Address and search bar', address.split('://', 1)[1])]
        snap = SimpleNamespace(id=f's{self.looks}', window_id=window_id, elements=elements, origin=(0, 0),
                               size=(1280, 800), scale=1.0)
        return None, snap, [], ''

    def act_on_element(self, window_id, index, action='click', snapshot_id=None, viewer=None, use_screen=False):
        self.clicked.append(self._elements(window_id)[1][index].name)
        self.in_call = True

    def press_key(self, window_id, keys, use_screen=False):
        self.windows.pop(window_id, None)


async def _join_through(browser, monkeypatch):
    from cognitrix.meetings import live

    class Desk:
        screen = live.Screen(browser, 'meeting:m1')

        async def do(self, fn, *args):
            return fn(*args)

    updates, alerts = [], []

    async def fake_set(_id, **fields):
        updates.append(fields)

    async def alert(row, title, message):
        alerts.append(title)

    monkeypatch.setattr(live, '_set', fake_set)
    monkeypatch.setattr(live, '_alert', alert)
    monkeypatch.setattr(live, 'JOIN_POLL_S', 0)
    row = SimpleNamespace(id='m1', url=browser.MEETING, bot_name='Ada', title='Standup', platform='meet')
    await asyncio.wait_for(live._join(live.Live('m1'), Desk(), row), 5)
    return updates, alerts


async def test_it_asks_the_person_to_sign_in_opens_the_page_and_then_joins(monkeypatch):
    from cognitrix.meetings import live

    browser = _SignInBrowser()
    updates, alerts = await _join_through(browser, monkeypatch)
    assert browser.opened[0] == browser.MEETING and browser.opened[1].startswith(live.GOOGLE_SIGN_IN)
    assert 'meet.google.com%2Fabc-defg-hij' in browser.opened[1] and len(browser.opened) == 2
    assert alerts == ['Sign in to Google for Standup']
    assert {'status': live.JOINING, 'progress': live.SIGN_IN_PROGRESS} in updates
    assert browser.clicked == ['Join now']


async def test_the_call_is_opened_again_when_google_lands_elsewhere_even_if_taken_over_then(monkeypatch):
    """After sign-in Google can send the browser to another page. The call is
    opened again, also when the person takes over just as that is tried
    (Codex review on PR #212: that used to end the meeting)."""
    # Looks: 1 Meet turns it away, 2 opening sign-in, 3-5 sign-in page, 6-8 the
    # other page (three looks to be sure), 9 going back to the call: taken over.
    browser = _SignInBrowser(lands_on='https://myaccount.google.com/', held_looks={9})
    _updates, alerts = await _join_through(browser, monkeypatch)
    assert browser.opened[-1] == browser.MEETING and len(browser.opened) == 3
    assert alerts == ['Sign in to Google for Standup']
    assert browser.clicked == ['Join now']


async def test_the_call_is_opened_again_when_the_person_took_over_before_googles_page_was_read(monkeypatch):
    """Codex review on PR #212: taken over before the first look at Google's
    sign-in window, signed in, handed back from the account page. The call's
    window was never found, and it used to wait there until the time ran out."""
    from cognitrix.meetings import live

    monkeypatch.setattr(live, 'REOPEN_AFTER_S', 0)
    browser = _SignInBrowser(lands_on='https://myaccount.google.com/', takes_over_at_google=True)
    _updates, alerts = await _join_through(browser, monkeypatch)
    assert browser.opened[-1] == browser.MEETING and len(browser.opened) == 3
    assert alerts == ['Sign in to Google for Standup'] and browser.clicked == ['Join now']


def test_a_window_just_opened_gets_time_to_load_before_the_call_is_opened_again():
    from cognitrix.meetings import live

    browser = _SignInBrowser()
    screen = live.Screen(browser, 'meeting:m1')
    screen.open(browser.MEETING)
    browser.windows[11] = 'https://workspace.google.com/loading'  # not there yet
    assert live.back_to_call(screen) is False and browser.opened == [browser.MEETING]
    browser.windows[11] = browser.MEETING
    assert live.back_to_call(screen) is True and browser.opened == [browser.MEETING]


def test_waiting_to_be_let_in_is_not_the_call_having_ended():
    """Meet's logo is a "Return to home screen" link on its join and waiting
    pages (seen on the real join page, Nov 28)."""
    from cognitrix.meetings import live

    logo = _el('link', 'Return to home screen')
    asking = _Page(logo, _el('heading', 'Asking to join…'), _el('static', "You'll join the call when someone lets you in"))
    assert live.join_step(asking, 'Ada', set()) == 'lobby'
    # ... also if the page had the button of the pages after a call.
    asking.elements[0] = _el('push button', 'Return to home screen')
    assert live.join_step(asking, 'Ada', set()) == 'lobby'
    # The logo alone (a page between two screens) says nothing.
    assert live.join_step(_Page(logo), 'Ada', set()) == 'working'
    # The pages after a call still count.
    removed = _Page(logo, _el('push button', 'Return to home screen'), _el('timer', 'Returning to home screen'))
    assert live.join_step(removed, 'Ada', set()) == 'ended'


def test_a_nickname_link_is_followed_to_its_room_past_other_restored_windows():
    """Codex review on PR #212: /lookup/team went on to a room code while
    Chromium also restored Gmail, and the room was not taken for the call."""
    from cognitrix.meetings import live

    addresses = {8: 'mail.google.com/mail/u/0/#inbox', 9: 'meet.google.com/abc-defg-hij'}

    class Restored(FakeDriver):
        def list_windows(self, query=None):
            return [SimpleNamespace(id=i, app='chromium', title='Chromium') for i in addresses] if self.url else []

        def look(self, window_id, *args, **kwargs):
            result = super().look(window_id, *args, **kwargs)
            result[1].elements = [_el('entry', 'Address and search bar', addresses[window_id])]
            return result

    screen = live.Screen(Restored(), 'meeting:1')
    screen.open('https://meet.google.com/lookup/team')
    assert screen.look().window_id == 9
    # Two rooms open: it can't tell which one the nickname went to.
    addresses[8] = 'meet.google.com/xyz-abcd-xyz'
    other = live.Screen(Restored(), 'meeting:2')
    other.open('https://meet.google.com/lookup/team')
    with pytest.raises(live.Stale):
        other.look()


def test_a_tab_that_cannot_be_read_as_the_call_is_not_closed(monkeypatch):
    """Codex review on PR #212: after a switch to another tab, an empty read of
    the window still let Ctrl+W through, which closes the tab in front."""
    from cognitrix.meetings import live

    monkeypatch.setattr(live, 'CLOSE_LOOK_GAP_S', 0)
    shown: list = []

    class Blank(FakeDriver):
        def look(self, window_id, *args, **kwargs):
            result = super().look(window_id, *args, **kwargs)
            result[1].elements = list(shown)
            return result

    driver = Blank()
    screen = live.Screen(driver, 'meeting:1')
    screen.open('https://meet.google.com/abc-defg-hij')
    screen.window_id = driver.window
    screen.close()
    assert driver.clicked == [] and driver.snapshots == live.CLOSE_LOOKS
    # An image whose address bar can't be read: the page itself shows it is the call.
    shown[:] = [_el('heading', 'Asking to join…'), _el('push button', 'Cancel')]
    screen.close()
    assert driver.clicked == ['ctrl+w']


def test_same_titled_windows_of_other_apps_are_not_told_apart_by_order():
    """Codex review on PR #212: GTK lists a window again at the end when it is
    hidden and shown, so order paired a window with another one's controls."""
    backend, infos = _backend(['Notes', 'Notes', 'Notes'], [(5, 'Notes'), (6, 'Notes'), (7, 'Notes')])
    for info in infos.values():
        info.app = 'gedit'
    assert all(backend._frame(info) is None for info in infos.values())
    # The one in front is still known by its active frame.
    from cognitrix.computer import linux
    backend.foreground = lambda: 6
    backend.atspi.states = lambda acc: {linux._ST_SHOWING} | ({linux._ST_ACTIVE} if acc.ptr == 'f2' else set())
    assert backend._frame(infos[6]).ptr == 'f2' and backend._frame(infos[5]) is None



# --- in the call: gaps, spoken commands, steering, questions, running notes ------


class SteerLLM:
    """Answers with a question for the owner, phrases things to raise, writes running notes."""

    def __init__(self):
        self.calls = []

    async def __call__(self, prompt, stream=False):
        self.calls.append(prompt)
        system = prompt[0]['content']
        if 'bring something up' in system:
            return SimpleNamespace(error=None, llm_response='One more thing: the budget review is due Friday.')
        if 'running notes' in system:
            return SimpleNamespace(error=None, llm_response='**So far**\nLaunch timing discussed.')
        if 'reply with "ASK: "' in system:
            return SimpleNamespace(error=None, llm_response='ASK: Can the launch move to Monday?')
        return SimpleNamespace(error=None, llm_response='Friday.')


async def _in_call(call, llm=None, **fields):
    """An agent already in a call, as the engine holds it."""
    live = call.live
    row = call.notes.MeetingNote(user_id='u1', kind='live', platform='meet', bot_name='Ada', status='live',
                                 url='https://meet.google.com/abc-defg-hij', **fields)
    await row.save()
    row = await call.notes.MeetingNote.get(row.id)
    item = live.Live(str(row.id))
    desk = live._Desk('u1', 'a1', 'Ada', row.id)
    desk.screen.open(row.url)
    call.driver.state = 'live'
    agent = SimpleNamespace(id='a1', name='Ada', llm=llm or call.llm, knowledge=[])
    settings = await call.extras.settings_for('u1')
    return live._Call(item, row, desk, agent, 'Sam', ('groq', 'whisper', 'key'), settings, [])


def _b64(text='x'):
    return base64.b64encode(text.encode()).decode()


def _saying(call, monkeypatch, *texts):
    said = iter(texts)

    async def transcribe(audio, **kwargs):
        return next(said)

    monkeypatch.setattr(call.notes, 'transcribe', transcribe)


async def test_speech_that_fails_to_transcribe_is_retried_then_marked_as_a_gap(call, monkeypatch):
    monkeypatch.setattr(call.live, 'TRANSCRIBE_BACKOFF_S', (0, 0))
    tries = []

    async def flaky(audio, **kwargs):
        tries.append(kwargs.get('hints'))
        if len(tries) < 3:
            raise call.notes.TranscriptionError('busy')
        return 'Third time lucky.'

    monkeypatch.setattr(call.notes, 'transcribe', flaky)
    c = await _in_call(call, title='Launch Review')
    await c._one(_b64(), '0:01')
    assert len(tries) == 3 and 'Third time lucky.' in c.lines[-1]
    # The names in the call go to speech-to-text as expected words.
    assert 'Ada' in tries[0] and 'Sam' in tries[0] and 'Launch' in tries[0]

    async def broken(audio, **kwargs):
        raise call.notes.TranscriptionError('down')

    monkeypatch.setattr(call.notes, 'transcribe', broken)
    await c._one(_b64(), '0:05')
    assert c.lines[-1] == '[0:05] (some speech here could not be transcribed)'
    assert 'could not be transcribed' in (await call.notes.MeetingNote.get(c.id)).transcript


async def test_anyone_in_the_call_can_pause_resume_mark_and_send_the_agent_away(call, monkeypatch):
    c = await _in_call(call)
    _saying(call, monkeypatch, 'Ada, pause the notes for a minute.', 'The acquisition price is 40 million.',
            'Ada, resume.', 'We go with plan B.', 'Ada, mark that as a decision.', 'Ada, you can leave now.')
    await c._one(_b64(), '0:01')
    assert c.paused and (await call.notes.MeetingNote.get(c.id)).paused
    assert any(t.startswith('Paused: nothing is kept until someone says') for t in call.driver.typed)
    await c._one(_b64(), '0:02')
    assert not any('40 million' in ln for ln in c.lines)
    await c._one(_b64(), '0:03')
    assert not c.paused and 'Back on: taking notes again.\n' in call.driver.typed
    await c._one(_b64(), '0:04')
    await c._one(_b64(), '0:05')
    assert c.lines[-1].endswith('(marked: decision — the line above)')
    await c._one(_b64(), '0:06')
    assert c.item.leave.is_set() and 'someone in the call asked it to leave' in c.reasons


async def test_private_steering_questions_for_the_owner_and_their_answer(call):
    llm = SteerLLM()
    c = await _in_call(call, llm)
    c.item.control.put_nowait(('note', "Don't commit to dates."))
    c.item.control.put_nowait(('raise', 'mention the budget review'))
    await c.take_control()
    assert (await call.notes.MeetingNote.get(c.id)).guidance == ["Don't commit to dates."]
    c.quiet_s = 0.5  # someone is talking: wait
    await c.raise_pending()
    assert c.to_raise
    c.quiet_s = 5
    await c.raise_pending()
    assert 'One more thing: the budget review is due Friday.\n' in call.driver.typed

    c.add('Ada, can the launch move to Monday?')
    await c.answer()
    asked = llm.calls[-1]
    assert "<private_guidance_from_owner>\n- Don't commit to dates." in asked[1]['content']
    row = await call.notes.MeetingNote.get(c.id)
    assert row.owner_question == 'Can the launch move to Monday?'
    assert 'Let me check that with Sam.\n' in call.driver.typed
    assert call.alerts[-1] == f'Question from {row.title}'

    c.item.control.put_nowait(('answer', 'Yes, Monday works.'))
    await c.take_control()
    row = await call.notes.MeetingNote.get(c.id)
    assert 'Sam says: Yes, Monday works.\n' in call.driver.typed
    assert row.owner_question == '' and row.owner_answer == 'Yes, Monday works.'


async def test_an_unanswered_question_is_let_go_after_a_while(call, monkeypatch):
    c = await _in_call(call, SteerLLM())
    await c.ask_owner('Is Monday OK?')
    monkeypatch.setattr(call.live, 'ASK_WAIT_S', -1)
    await c.question_timeout()
    assert not c.question and any("hasn't answered yet" in t for t in call.driver.typed)


async def test_running_notes_and_watch_word_alerts(call, monkeypatch):
    monkeypatch.setattr(call.live, 'LIVE_NOTES_S', 0)
    monkeypatch.setattr(call.live, 'LIVE_NOTES_MIN_LINES', 2)
    c = await _in_call(call, SteerLLM(), watch_words=['budget'])
    _saying(call, monkeypatch, 'We are over budget again.', 'The budget needs a look.')
    await c._one(_b64(), '0:01')
    await c._one(_b64(), '0:02')
    assert call.alerts.count(f'"budget" came up in {c.row.title}') == 1  # once per few minutes
    assert c.notes_due()
    c.write_live_notes()
    await c.notes_task
    assert (await call.notes.MeetingNote.get(c.id)).live_notes.startswith('**So far**')
    assert not c.notes_due()


async def test_a_dropped_agent_rejoins_but_not_a_call_that_ended(call, monkeypatch):
    c = await _in_call(call)
    clicked = call.driver.act_on_element

    def act(window_id, index, *args, **kwargs):
        if call.driver._elements()[index].name == 'Rejoin':
            call.driver.state = 'live'
        return clicked(window_id, index, *args, **kwargs)

    monkeypatch.setattr(call.driver, 'act_on_element', act)
    call.driver.state = 'ended'  # "You left the meeting" with a Rejoin button
    assert await c.check_screen()
    row = await call.notes.MeetingNote.get(c.id)
    assert row.rejoins == 1 and row.status == call.live.LIVE and '(back in the call)' in c.lines[-1]

    c.rejoins = call.live.MAX_REJOINS
    call.driver.state = 'ended'
    assert not await c.check_screen() and c.ended

    c2 = await _in_call(call)
    monkeypatch.setattr(call.driver, '_elements', lambda: [_el('heading', 'The meeting has ended'),
                                                           _el('push button', 'Rejoin')])
    assert not await c2.check_screen() and c2.rejoins == 0


async def test_a_call_in_progress_is_picked_back_up_after_a_restart(call, monkeypatch):
    held = asyncio.Event()
    started = []

    async def run(item):
        started.append((item.meeting_id, item.resume))
        await held.wait()

    monkeypatch.setattr(call.live, 'run', run)
    in_call = call.notes.MeetingNote(user_id='u1', kind='live', status=call.live.LIVE, agent_id='a1',
                                     url='https://meet.google.com/abc-defg-hij', progress='listening')
    joining = call.notes.MeetingNote(user_id='u1', kind='live', status=call.live.JOINING, agent_id='a1',
                                     url='https://meet.google.com/xyz-defg-hij')
    await in_call.save()
    await joining.save()
    try:
        assert await call.live.resume_interrupted() == 1
        await asyncio.sleep(0)
        assert started == [(str(in_call.id), True)]
        assert (await call.notes.MeetingNote.get(in_call.id)).status == call.live.LIVE
        assert (await call.notes.MeetingNote.get(joining.id)).status == call.notes.FAILED
    finally:
        held.set()
        await asyncio.sleep(0)


async def test_a_failed_join_keeps_what_the_agent_saw_and_tells_the_chat(call, monkeypatch):
    reported = []

    async def report(row, *, notes='', error=''):
        reported.append(error)
        return True

    monkeypatch.setattr(call.extras, 'report_to_chat', report)
    row = call.notes.MeetingNote(user_id='u1', kind='live', platform='meet', bot_name='Ada', status='joining',
                                 url='https://meet.google.com/abc-defg-hij', session_id='s1')
    await row.save()
    desk = call.live._Desk('u1', 'a1', 'Ada', row.id)
    desk.screen.open(row.url)
    await call.live._failed(row, desk, False, None, [], 'Nobody let it in.')
    saved = await call.notes.MeetingNote.get(row.id)
    assert saved.status == call.notes.FAILED and 'push button: Ask to join' in saved.seen
    assert 'Address: https://meet.google.com/abc-defg-hij' in saved.seen
    assert reported == ['Nobody let it in.']


async def test_the_test_call_reports_each_check(call):
    c = await _in_call(call, test=True, title='Test call')
    c.reasons.append('spoke')
    c.add('Ada (chat): Hello')
    c.add('Testing one two three. This is Ada checking the sound.')
    await call.live._finish_test(c)
    row = await call.notes.MeetingNote.get(c.id)
    assert row.status == call.notes.DONE and row.notes.count('✓') == 4
    assert call.alerts[-1] == 'Test call passed'

    c2 = await _in_call(call, test=True, title='Test call')
    await call.live._finish_test(c2)
    row = await call.notes.MeetingNote.get(c2.id)
    assert '✗ Spoke out loud' in row.notes and 'read-aloud key' in row.notes
    assert call.alerts[-1] == 'Test call found a problem'


async def test_notes_go_back_to_the_chat_that_sent_the_agent(call, monkeypatch):
    from cognitrix.sessions import agent_chats

    sent = []

    async def deliver(user, agent_id, session_id, text, **kwargs):
        sent.append((user, agent_id, session_id, text))
        return 'started'

    monkeypatch.setattr(agent_chats, 'deliver_report', deliver)
    row = call.notes.MeetingNote(user_id='u1', kind='live', title='Launch', bot_name='Ada', agent_id='a1',
                                 session_id='s1', chat_agent_id='a9')
    notes = '## Summary\nShip Friday.\n\n## Action items\n- **Kofi** - send the deck\n- book the room\n'
    assert await call.extras.report_to_chat(row, notes=notes)
    user, agent_id, session_id, text = sent[0]
    assert (user, agent_id, session_id) == ('u1', 'a9', 's1')
    assert '- Kofi: send the deck' in text and '- Unassigned: book the room' in text
    assert 'only after they say yes' in text
    assert not await call.extras.report_to_chat(call.notes.MeetingNote(user_id='u1', title='x'), notes=notes)


async def test_meeting_settings_background_and_search_from_the_page(call, monkeypatch):
    from cognitrix.api.routes import meetings as routes

    with pytest.raises(HTTPException) as err:
        await routes.put_settings(routes.MeetingSettingsBody(share='everything'), _ctx())
    assert err.value.status_code == 400
    saved = await routes.put_settings(routes.MeetingSettingsBody(share='work', vocabulary='Kubernetes, Akosua'),
                                      _ctx())
    assert saved['share'] == 'work' and (await routes.get_settings(_ctx()))['vocabulary'] == 'Kubernetes, Akosua'

    before = call.notes.MeetingNote(user_id='u1', kind='live', title='Weekly sync', status='done',
                                    notes='## Summary\nOk.\n\n## Action items\n- **Kofi** - send the deck\n')
    await before.save()
    monkeypatch.setattr(call.live, 'start', lambda meeting_id, **kw: None)
    made = await routes.join_live(routes.LiveJoin(url='https://meet.google.com/abc', title='Weekly sync',
                                                  context='Agenda: launch', watch_words='budget, hiring'), _ctx())
    assert made['share'] == 'work' and made['watch_words'] == ['budget', 'hiring']
    assert made['context'].startswith('Agenda: launch') and '**Kofi** - send the deck' in made['context']

    found = await routes.search_meetings('deck Kofi', _ctx())
    assert [r['id'] for r in found['results']] == [str(before.id)]
    assert (await routes.search_meetings('deck', _ctx('u2')))['results'] == []

    with pytest.raises(HTTPException) as err:
        await routes.steer_live(made['id'], routes.Steer(kind='note', text='hi'), _ctx())
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        await routes.steer_live(made['id'], routes.Steer(kind='shout', text='hi'), _ctx())
    assert err.value.status_code == 400

    test = await routes.test_call(routes.TestCall(), _ctx())
    assert test['test'] and test['url'].startswith('http://127.0.0.1:') and test['url'].endswith('/test-call')


async def test_steer_and_search_tools(call, monkeypatch):
    from cognitrix.tools.meetings import search_meetings, steer_meeting
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    row = call.notes.MeetingNote(user_id='u1', kind='live', title='Pricing call', agent_id='a1', status='done',
                                 notes='## Decisions\n- Prices go up in March.')
    await row.save()
    steered = []
    monkeypatch.setattr(call.live, 'steer', lambda mid, kind, text='': steered.append((mid, kind, text)) or True)
    token = set_execution_context(ToolExecutionContext(user_id='u1', agent_id='a1', session_id='s1'))
    try:
        out = await steer_meeting.run(text='Ask about churn', kind='raise', parent=SimpleNamespace(id='a1'))
        assert 'next pause' in str(out.content) and steered == [(str(row.id), 'raise', 'Ask about churn')]
        out = await search_meetings.run(query='prices March', parent=SimpleNamespace(id='a1'))
        assert '"Pricing call"' in str(out.content)
    finally:
        reset_execution_context(token)

    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel

    for name in ('Steer Meeting', 'Search Meetings'):
        assert DestructiveOpDetector().analyze(name, {}).risk_level == RiskLevel.LOW


async def test_speech_heard_while_paused_stays_dropped_even_if_notes_resume_before_it_is_transcribed(call, monkeypatch):
    c = await _in_call(call)
    # Paused from the page: pieces heard from now on are off the record.
    await c.set_paused(True, by='Sam')
    c.piece_no = 1  # one piece arrives while paused, still waiting to be transcribed
    await c.set_paused(False, by='Sam')  # resumed before its text came back
    _saying(call, monkeypatch, 'The acquisition price is 40 million.', 'Next item is hiring.')
    await c._one(_b64(), '0:02', 1)
    c.piece_no = 2
    await c._one(_b64(), '0:03', 2)
    assert not any('40 million' in ln for ln in c.lines) and 'hiring' in c.lines[-1]

    # Said in the call: pieces already queued after "pause notes" are dropped too.
    c2 = await _in_call(call)
    c2.piece_no = 3  # "pause notes", then two more pieces queued before its text came back
    _saying(call, monkeypatch, 'Ada, pause the notes.', 'Salary bands are 90 to 120.', 'Still private.',
            'Back to the roadmap.')
    await c2._one(_b64(), '0:01', 1)
    await c2.set_paused(False, by='Sam')  # the page resumes while pieces 2 and 3 wait
    await c2._one(_b64(), '0:02', 2)
    await c2._one(_b64(), '0:03', 3)
    c2.piece_no = 4
    await c2._one(_b64(), '0:04', 4)
    text = '\n'.join(c2.lines)
    assert 'Salary' not in text and 'Still private' not in text and 'roadmap' in c2.lines[-1]


async def test_a_lost_connection_without_a_rejoin_button_is_reopened_after_a_few_checks(call, monkeypatch):
    c = await _in_call(call)
    dropped = {'on': True}
    elements = call.driver._elements
    opened = call.driver.open

    def look_elements():
        return [_el('heading', 'Connection lost')] if dropped['on'] else elements()

    def reopen(target, args=None, timeout_s=12.0):
        dropped['on'] = False
        return opened(target, args, timeout_s)

    monkeypatch.setattr(call.driver, '_elements', look_elements)
    monkeypatch.setattr(call.driver, 'open', reopen)
    for _ in range(call.live.LOST_CHECKS - 1):
        assert await c.check_screen() and c.rejoins == 0
    assert await c.check_screen()
    assert c.rejoins == 1 and not dropped['on'] and '(back in the call)' in c.lines[-1]


async def test_only_one_process_takes_over_a_call_after_a_restart(call, monkeypatch):
    held = asyncio.Event()

    async def run(item):
        await held.wait()

    monkeypatch.setattr(call.live, 'run', run)
    row = call.notes.MeetingNote(user_id='u1', kind='live', status=call.live.LIVE, agent_id='a1', runner='old',
                                 url='https://meet.google.com/abc-defg-hij', progress='listening')
    await row.save()
    stale = await call.notes.MeetingNote.find({'kind': 'live'})  # what a second process read at the same time
    try:
        assert await call.live.resume_interrupted() == 1
        assert (await call.notes.MeetingNote.get(row.id)).runner == call.live.BOOT
        assert await call.live.resume_interrupted() == 0  # already ours
        other = AsyncFind(stale)
        monkeypatch.setattr(call.live, 'BOOT', 'second-process')
        monkeypatch.setattr(call.notes.MeetingNote, 'find', other)
        monkeypatch.setattr(call.live, '_live', {})
        monkeypatch.setattr(call.live, 'fail_interrupted', other.nothing)
        assert await call.live.resume_interrupted() == 0
    finally:
        held.set()
        await asyncio.sleep(0)


class AsyncFind:
    def __init__(self, rows):
        self.rows = rows

    async def __call__(self, *args, **kwargs):
        return self.rows

    async def nothing(self):
        return None
