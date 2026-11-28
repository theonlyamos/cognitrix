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


def _backend(names, windows):
    from cognitrix.computer import WindowInfo, linux

    backend = object.__new__(linux.LinuxBackend)
    backend.atspi = _Atspi(42, names)
    infos = {wid: WindowInfo(id=wid, title=title, app='chromium', pid=42, minimized=False, rect=(0, 0, 1280, 800))
             for wid, title in windows}
    backend._client_windows = lambda: list(infos)
    backend._info = infos.get
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
    # Signed out, guests not allowed: the person is asked to sign in.
    turned_away = _Page(_el('heading', "You can't join this video call"), _el('push button', 'Return to home screen'))
    assert live.join_step(turned_away, 'Ada', set()) == 'sign in'
    # Signed in and still turned away: that is the host's choice.
    signed_in = _Page(_el('heading', "You can't join this video call"),
                      _el('push button', 'Google Account: Ada (ada@example.com)'))
    assert live.join_step(signed_in, 'Ada', set()).startswith('refused')
    # Google's sign-in page itself.
    google = _Page(_el('page tab', 'Sign in - Google Accounts - Memory usage - 88.4 MB'),
                   _el('entry', 'Email or phone'), _el('push button', 'Next'))
    assert live.join_step(google, 'Ada', set()) == 'sign in' and not google.typed and not google.clicked
    # Meet's current wording while the host decides.
    asking = _Page(_el('heading', 'Asking to join…'), _el('static', "You'll join the call when someone lets you in"))
    assert live.join_step(asking, 'Ada', set()) == 'lobby'
    # In the call even when the Leave button is past what one look reads.
    inside = _Page(_el('static', "You're the only one here"), _el('toggle button', 'Raise hand'))
    assert live.join_step(inside, 'Ada', set()) == 'live'
    ended = _Page(_el('heading', 'You left the meeting'), _el('push button', 'Rejoin'))
    assert live.join_step(ended, 'Ada', set()) == 'ended'


def test_a_button_that_does_nothing_is_not_pressed_forever():
    from cognitrix.meetings import live

    page = _Page(_el('push button', 'Got it'), _el('push button', 'Join now'))
    clicks: dict[str, int] = {}
    for _ in range(live.MAX_SAME_CLICKS + 1):
        live.join_step(page, 'Ada', set(), clicks)
    assert page.clicked == ['Got it'] * live.MAX_SAME_CLICKS + ['Join now']


async def test_it_waits_for_the_person_to_sign_in_then_joins(monkeypatch):
    from cognitrix.computer import HumanControl
    from cognitrix.meetings import live

    pages = {
        'meet-out': [_el('heading', "You can't join this video call")],
        'google': [_el('page tab', 'Sign in - Google Accounts'), _el('entry', 'Email or phone')],
        'prejoin': [_el('push button', 'Join now')],
        'call': [_el('push button', 'Leave call')],
    }
    state = {'page': 'meet-out', 'looks': 0}
    opened, clicked = [], []

    class Screen:
        def open(self, url):
            opened.append(url)
            state['page'] = 'google' if 'accounts.google.com' in url else 'meet-out'

        def close(self):
            pass

        def look(self):
            state['looks'] += 1
            if state['page'] == 'google' and state['looks'] in (4, 5):
                raise HumanControl('Ama has taken over this computer.')  # the person signs in
            if state['page'] == 'google' and state['looks'] > 5:
                state['page'] = 'prejoin'  # Google sends the browser back to the call
            return live.View(1, f's{state["looks"]}', pages[state['page']])

        def click(self, view, index):
            clicked.append(view.elements[index].name)
            state['page'] = 'call'

        def type_into(self, view, index, text, submit=False):
            pass

    class Desk:
        screen = Screen()

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
    row = SimpleNamespace(id='m1', url='https://meet.google.com/abc-defg-hij', bot_name='Ada', title='Standup',
                          platform='meet')
    await asyncio.wait_for(live._join(live.Live('m1'), Desk(), row), 5)

    assert opened[0] == row.url and opened[1].startswith(live.GOOGLE_SIGN_IN)
    assert 'meet.google.com%2Fabc-defg-hij' in opened[1]
    assert alerts == ['Sign in to Google for Standup']
    assert {'status': live.WAITING, 'progress': live.SIGN_IN_PROGRESS} in updates
    assert any(u.get('progress') == 'signed in; joining' for u in updates)
    assert clicked == ['Join now']
