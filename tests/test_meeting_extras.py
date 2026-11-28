"""Meeting extras without a call: spoken commands, calendar background,
expected words, speaker transcripts, action items and answers that ask the
owner (cognitrix/meetings/extras.py, notes.py, providers/transcription.py)."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('said, expected', [
    ('Ada, pause the notes for a bit', ('pause', '')),
    ("Ada, let's go off the record", ('pause', '')),
    ('Ada, stop recording please', ('pause', '')),
    ('Ada, resume', ('resume', '')),
    ('Ada, back on the record', ('resume', '')),
    ('Ada, you can leave now', ('leave', '')),
    ('Ada, please leave the call', ('leave', '')),
    ('Ada, mark that as an action item', ('mark', 'action item')),
    ('Ada, note that as a decision', ('mark', 'decision')),
    ("Ada, what's the launch date?", None),
])
def test_spoken_commands(said, expected):
    from cognitrix.meetings import extras

    assert extras.command(said) == expected


def test_watch_words_match_whole_words_only():
    from cognitrix.meetings import extras

    assert extras.watch_hits('The Budget is tight; hiring paused', ['budget', 'hiring', 'hire']) == ['budget', 'hiring']
    assert extras.words('a, b\nA; c') == ['a', 'b', 'c']


def test_calendar_background_has_the_agenda_and_names_but_no_addresses_or_links():
    from cognitrix.meetings import extras

    event = {
        'description': '<p>1. Launch date<br>2. Budget</p><p>Join with Google Meet: https://meet.google.com/x</p>',
        'attendees': [{'displayName': 'Kofi Mensah', 'email': 'kofi@example.com'},
                      {'email': 'akosua.boateng@example.com'},
                      {'email': 'room-1@resource.calendar.google.com', 'resource': True}],
    }
    background, names = extras.event_context(event)
    assert '1. Launch date\n2. Budget' in background and 'meet.google.com' not in background
    assert names == ['Kofi Mensah', 'akosua boateng'] and '@' not in background


def test_previous_meeting_context_keeps_the_open_items():
    from cognitrix.meetings import extras
    from cognitrix.meetings.notes import MeetingNote

    row = MeetingNote(user_id='u1', title='Weekly sync', notes=(
        '## Summary\nFine.\n\n## Decisions\nNone recorded.\n\n## Action items\n- **Kofi** - send the deck\n\n'
        '## Open questions\n- Who owns pricing?\n'))
    text = extras.previous_context(row)
    assert text.startswith('Last time ("Weekly sync"')
    assert 'Action items:\n- **Kofi** - send the deck' in text and 'Who owns pricing?' in text
    assert 'Decisions' not in text
    assert extras.previous_context(None) == ''


def test_expected_words_come_from_the_call_and_the_settings():
    from cognitrix.meetings import extras
    from cognitrix.meetings.notes import MeetingNote

    row = MeetingNote(user_id='u1', title='Kubernetes Migration', bot_name='Ada Notes',
                      context='Agenda\n\nInvited: Kofi Mensah, Akosua')
    settings = extras.MeetingSettings(user_id='u1', vocabulary='Tailscale, Cognitrix')
    out = extras.hints(row, 'Ada', 'Sam', settings)
    for word in ('Ada Notes', 'Ada', 'Sam', 'Kofi Mensah', 'Akosua', 'Kubernetes', 'Migration', 'Tailscale'):
        assert word in out


def test_action_items_are_read_from_the_notes():
    from cognitrix.meetings import notes

    text = ('## Summary\nOk\n\n## Action items\n- **Kofi** - send the deck\n* **Ama**: book the room\n'
            '- call the vendor\n**Sam** — update the plan\n\n## Open questions\n- x')
    assert notes.action_items(text) == [
        {'owner': 'Kofi', 'what': 'send the deck'}, {'owner': 'Ama', 'what': 'book the room'},
        {'owner': 'Unassigned', 'what': 'call the vendor'}, {'owner': 'Sam', 'what': 'update the plan'},
    ]
    assert notes.action_items('## Action items\nNone recorded.') == []


def test_hints_and_speakers_for_deepgram():
    from cognitrix.providers import transcription as t

    assert t.hint_text(['Ada', 'ada', '', 'Kofi']) == 'Ada, Kofi'
    params = t._deepgram_params('nova-3', 'en', 'Ada, Kofi', True)
    assert ('keyterm', 'Ada') in params and ('keyterm', 'Kofi') in params
    assert ('diarize', 'true') in params and ('language', 'en') in params
    assert ('keywords', 'Ada') in t._deepgram_params('nova-2', None, 'Ada', False)
    data = {'results': {'utterances': [{'speaker': 0, 'transcript': 'Hi all.'},
                                       {'speaker': 0, 'transcript': 'Shall we start?'},
                                       {'speaker': 1, 'transcript': 'Yes.'}]}}
    assert t._deepgram_speakers(data) == 'Speaker 1: Hi all. Shall we start?\nSpeaker 2: Yes.'


async def test_speakers_need_a_provider_that_tells_them_apart():
    from cognitrix.providers import transcription as t

    with pytest.raises(t.TranscriptionError):
        await t.transcribe_speakers(b'RIFF', mime='audio/wav', provider='groq', model='whisper', api_key='k')


async def test_an_answer_can_become_a_question_for_the_owner_only_when_allowed():
    from cognitrix.meetings import live

    async def llm(prompt, stream=False):
        return SimpleNamespace(error=None, llm_response='ASK: Is Monday OK?')

    args = dict(agent='Ada', owner='Sam', name='Ada', platform='meet', mode='chat', lines=['Ada, Monday?'])
    assert await live.compose_answer(llm, can_ask=True, **args) == ('ask', 'Is Monday OK?')
    assert await live.compose_answer(llm, can_ask=False, **args) == ('', '')


async def test_notes_use_the_background_and_the_speaker_transcript():
    from cognitrix.meetings import notes

    seen = []

    async def llm(prompt, stream=False):
        seen.append(prompt[1]['content'])
        return SimpleNamespace(error=None, llm_response='## Summary\nDone.')

    await notes.write_notes(llm, 'We agreed to ship on Friday and Kofi sends the deck.', 'Launch',
                            context='Agenda: launch', speakers='Speaker 1: We ship Friday.')
    assert '<background>\nAgenda: launch\n</background>' in seen[0]
    assert '<transcript_with_speakers>\nSpeaker 1: We ship Friday.' in seen[0]


async def test_look_ups_gather_memory_knowledge_meetings_and_busy_times(monkeypatch):
    from cognitrix import knowledge
    from cognitrix.meetings import extras
    from cognitrix.memory import saved

    async def memory(user_id, agent, query, k=6):
        return [SimpleNamespace(content='Sam prefers Tuesday launches.')]

    async def know(user_id, collections, query, limit):
        return [{'collection': 'handbook', 'text': 'Launches need a two-day freeze.'}]

    async def meetings(user_id, query, limit=10, *, exclude=''):
        return [{'title': 'Weekly sync', 'date': '2026-11-20', 'snippet': 'launch moved'}]

    async def busy(user_id):
        return 'Calendar, busy times: Mon 09:00-10:00'

    monkeypatch.setattr(saved, 'search', memory)
    monkeypatch.setattr(knowledge, 'search', know)
    monkeypatch.setattr(extras, 'search', meetings)
    monkeypatch.setattr(extras, 'busy_times', busy)
    agent = SimpleNamespace(knowledge=['handbook'])
    text = await extras.lookup('u1', agent, 'Is Sam free on Monday for the launch?')
    for part in ('Sam prefers Tuesday', 'two-day freeze', '"Weekly sync"', 'Mon 09:00-10:00'):
        assert part in text
    assert 'busy times' not in await extras.lookup('u1', agent, 'What did we decide about pricing?')


def test_the_sandbox_serves_the_practice_call_and_says_when_an_agent_is_in_a_call(monkeypatch):
    import threading
    import urllib.error
    import urllib.request

    from cognitrix.computer.sandbox import supervisor

    monkeypatch.setenv('COGNITRIX_SANDBOX_TOKEN', 'secret')
    recorder = SimpleNamespace(alive=True)
    desk = SimpleNamespace(held_by=None, audio=SimpleNamespace(recorder=recorder))
    sup = SimpleNamespace(desktops={'a1': desk})
    activity = SimpleNamespace(in_flight=0)
    assert supervisor.busy_reasons(sup, None, activity) == ['an agent is in a call']
    recorder.alive = False
    assert supervisor.busy_reasons(sup, None, activity) == []

    server = supervisor.make_server(sup, port=0, host='127.0.0.1')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}'
        with urllib.request.urlopen(base + '/test-call', timeout=5) as page:  # noqa: S310
            html = page.read().decode()
        assert page.status == 200 and 'Join now' in html and 'Leave call' in html and 'Send a message' in html
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + '/desktops', timeout=5)  # noqa: S310
        assert err.value.code == 401
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('said', [
    'Ada, do not resume notes yet',
    'Ada, can we continue the budget discussion?',
    "Ada, don't leave the call",
    "Ada, please don't pause the notes",
    'We should resume the hiring plan next week',
])
def test_ordinary_speech_is_not_a_command(said):
    from cognitrix.meetings import extras

    assert extras.command(said) is None


def test_a_spoken_pause_keeps_only_what_came_before_it():
    from cognitrix.meetings import extras

    said = 'Ada, pause the notes. The acquisition price is 40 million.'
    assert extras.up_to_command(said) == 'Ada, pause the notes'
    said = 'Ada, pause the notes, the private forecast is four million.'
    assert extras.up_to_command(said) == 'Ada, pause the notes'
    assert extras.up_to_command('No command here.') == 'No command here.'


@pytest.mark.parametrize('said, order', [
    ('Ada, when should we resume taking notes?', None),
    ('Ada, should we resume the notes?', None),
    ('Ada, I think we can resume later', None),
    ('Ada, do not resume notes yet', None),
    ('Ada, continue the budget discussion', None),
    ('Ada resume', 'resume'),
    ('Ada, please resume.', 'resume'),
    ('Ada, can you resume taking notes?', 'resume'),
    ("Ada, we're back on the record", 'resume'),
    ('Ada, off the record', 'pause'),
    ('Ada, we are going off the record now', 'pause'),
    ('Ada, pause the notes, the private forecast is four million.', 'pause'),
])
def test_spoken_commands_only_resume_on_a_request(said, order):
    from cognitrix.meetings import extras

    got = extras.command(said, ['Ada'])
    assert (got[0] if got else None) == order


def test_the_recorder_keeps_speech_until_the_engine_has_its_text():
    from collections import deque

    from cognitrix.computer.sandbox import audio

    rec = audio.Recorder.__new__(audio.Recorder)
    rec._guard = __import__('threading').Lock()
    rec._pieces = deque({'seq': n, 'audio': ''} for n in (1, 2, 3))
    assert [p['seq'] for p in rec.pieces_after(2, ack=0)] == [3]  # 1 and 2 handed over, not yet transcribed
    assert [p['seq'] for p in rec.pieces_after(0, ack=0)] == [1, 2, 3]  # a restarted engine reads them again
    assert [p['seq'] for p in rec.pieces_after(3, ack=2)] == []
    assert [p['seq'] for p in rec._pieces] == [3]
    assert [p['seq'] for p in rec.pieces_after(3)] == [] and not rec._pieces  # older engines: as before


async def test_existing_postgresql_and_mysql_tables_get_the_meeting_columns():
    from cognitrix.config import _migrate_relational_task_schema
    from cognitrix.meetings.notes import MeetingNote

    for dbms in ('postgresql', 'mysql'):
        statements = []

        class Cursor:
            def __init__(self, rows=()):
                self.rows = rows

            def fetchall(self):
                return list(self.rows)

        class Recorder:
            pass

        async def query(statement, params=None, _s=statements):
            _s.append(statement)
            if 'pg_index' in statement.lower() or 'show index' in statement.lower():
                return Cursor([(True, ['run_id', 'sequence'])])
            return Cursor()

        Recorder.dbms, Recorder.query = dbms, staticmethod(query)
        try:
            await _migrate_relational_task_schema(Recorder)
        except RuntimeError as exc:
            assert 'taskrunevents' in str(exc)  # the fake can't describe MySQL's index; meetings ran before it
        sql = '\n'.join(statements).lower()
        for field in MeetingNote.model_fields:
            if field in ('id', 'user_id', 'title', 'filename', 'agent_id', 'language', 'status', 'progress',
                         'seconds', 'transcript', 'notes', 'error', 'created_at', 'updated_at'):
                continue
            assert 'alter table meetingnotes add column' in sql and f' {field} ' in sql, (dbms, field)
        assert 'default 1' not in sql.split('meetingnotes', 1)[1].split('\n')[0]


def test_search_and_read_tools_are_offered_without_the_computer():
    import cognitrix.tools  # noqa: F401  (registers the tools)
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.meetings import MEETING_TOOL_CATEGORY, OFFLINE_MEETING_TOOL_NAMES

    names = [t.name for t in ToolManager.get_tools_by_category(MEETING_TOOL_CATEGORY)
             if t.name.lower().replace(' ', '_') in OFFLINE_MEETING_TOOL_NAMES]
    assert sorted(names) == ['Meeting Transcript', 'Search Meetings', 'Upcoming Calls']


def test_a_tab_that_is_not_the_call_is_never_kept_as_what_the_agent_saw():
    from cognitrix.meetings import live

    class Driver:
        def look(self, window_id, query=None, limit=60, viewer=None):
            elements = [SimpleNamespace(role='entry', name='Address and search bar', value='https://mail.google.com/',
                                        enabled=True, rect=(0, 0, 1, 1))]
            return 'IMAGE', SimpleNamespace(id='s1', elements=elements, origin=(0, 0), scale=1.0), [], ''

    screen = live.Screen(Driver(), 'meeting:1')
    screen.platform, screen.url, screen.window_id = 'meet', 'https://meet.google.com/abc-defg-hij', 5
    with pytest.raises(live.Stale):
        screen.look()
    assert screen.last_view is None and screen.last_image is None


def test_restart_recovery_only_accepts_this_meetings_room():
    from cognitrix.meetings import live

    def view(address):
        return live.View(1, 's', [SimpleNamespace(role='entry', name='Address and search bar', value=address,
                                                  enabled=True, rect=(0, 0, 1, 1))])

    screen = live.Screen(SimpleNamespace(), 'meeting:1')
    screen.platform, screen.url = 'zoom', 'https://us02web.zoom.us/wc/join/81234567890'
    win = SimpleNamespace(title='Zoom')
    assert live._is_this_call(screen, view('https://us02web.zoom.us/wc/81234567890/start'), win)
    assert not live._is_this_call(screen, view('https://us02web.zoom.us/wc/89999999999/start'), win)
    screen.platform, screen.url = 'teams', 'https://teams.microsoft.com/l/meetup-join/abc'
    assert not live._is_this_call(screen, view('https://us02web.zoom.us/wc/81234567890/start'), win)
    screen.platform, screen.url = 'meet', 'https://meet.google.com/abc-defg-hij'
    assert live._is_this_call(screen, view('https://meet.google.com/abc-defg-hij'), win)
    assert not live._is_this_call(screen, view('https://meet.google.com/zzz-zzzz-zzz'), win)


def test_search_looks_in_both_transcripts():
    import asyncio

    from cognitrix.meetings import extras
    from cognitrix.meetings.notes import MeetingNote

    row = MeetingNote(id='m1', user_id='u1', title='Sync', notes='## Summary\nOk',
                      transcript='[0:01] Ada (chat): The invoice goes out Friday.',
                      speaker_transcript='Speaker 1: Hello all.')

    async def find(query):
        return [row]

    original = MeetingNote.find
    MeetingNote.find = staticmethod(find)
    try:
        hits = asyncio.run(extras.search('u1', 'invoice Friday'))
    finally:
        MeetingNote.find = original
    assert hits and 'invoice goes out Friday' in hits[0]['snippet']


def _address_view(address):
    from cognitrix.meetings import live

    return live.View(1, 's', [SimpleNamespace(role='entry', name='Address and search bar', value=address,
                                              enabled=True, rect=(0, 0, 1, 1))])


def test_restart_recovery_compares_the_exact_meeting():
    from cognitrix.meetings import live

    screen = live.Screen(SimpleNamespace(), 'meeting:1')
    win = SimpleNamespace(title='')
    cases = [
        ('teams', 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_AAA%40thread.v2/0',
         'https://teams.microsoft.com/l/meetup-join/19%3ameeting_BBB%40thread.v2/0'),
        ('zoom', 'https://us02web.zoom.us/wc/join/8123456789', 'https://us02web.zoom.us/wc/81234567890/start'),
        ('zoom', 'https://zoom.us/my/alice', 'https://zoom.us/my/bob'),
        ('other', 'https://meet.jit.si/TeamSync', 'https://meet.jit.si/BoardRoom'),
        ('other', 'https://calls.example.com/room/1', 'https://other.example.org/room/1'),
    ]
    for platform, link, elsewhere in cases:
        screen.platform, screen.url = platform, link
        assert live._is_this_call(screen, _address_view(link), win), link
        assert not live._is_this_call(screen, _address_view(elsewhere), win), (link, elsewhere)
    # A page whose address doesn't say which meeting it is never counts.
    screen.platform, screen.url = 'teams', cases[0][1]
    assert not live._is_this_call(screen, _address_view('https://teams.microsoft.com/v2/'), win)
    # A Meet nickname link: the room it resolved to when the agent got in was kept.
    screen = live.Screen(SimpleNamespace(), 'meeting:1')
    screen.platform, screen.url = 'meet', 'https://meet.google.com/lookup/teamsync'
    assert not live._is_this_call(screen, _address_view('https://meet.google.com/abc-defg-hij'), win)
    screen._room_code = 'abc-defg-hij'
    assert live._is_this_call(screen, _address_view('https://meet.google.com/abc-defg-hij'), win)
    assert not live._is_this_call(screen, _address_view('https://meet.google.com/zzz-zzzz-zzz'), win)


async def test_after_a_restart_the_kept_room_is_used_and_the_person_at_the_computer_is_waited_for(monkeypatch):
    from cognitrix.computer import HumanControl
    from cognitrix.meetings import live
    from cognitrix.meetings.notes import MeetingNote

    in_call = [SimpleNamespace(role='button', name='Leave call', value='', enabled=True, rect=(0, 0, 1, 1)),
               SimpleNamespace(role='entry', name='Address and search bar', value='https://meet.google.com/abc-defg-hij',
                               enabled=True, rect=(0, 0, 1, 1))]
    state = {'held': 2}

    class Driver:
        def list_windows(self):
            if state['held']:
                state['held'] -= 1
                raise HumanControl('The person has the computer.')
            return [SimpleNamespace(id=7, app='chromium', title='Meet')]

        def look(self, window_id, query=None, limit=60, viewer=None):
            return None, SimpleNamespace(id='s', elements=in_call, origin=(0, 0), scale=1.0), [], ''

    class Desk:
        def __init__(self):
            self.driver = Driver()
            self.screen = live.Screen(self.driver, 'meeting:1')

        async def do(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

    writes = []

    async def fake_set(meeting_id, **fields):
        writes.append(fields)

    monkeypatch.setattr(live, '_set', fake_set)
    monkeypatch.setattr(live, 'HANDBACK_POLL_S', 0)
    row = MeetingNote(user_id='u1', kind='live', platform='meet', url='https://meet.google.com/lookup/teamsync',
                      call_state={'room': 'meet:abc-defg-hij'})
    desk = Desk()
    assert await live._back_in_call(live.Live('m1'), desk, row)
    assert desk.screen.window_id == 7 and state['held'] == 0
    assert writes == [{'progress': 'waiting for you to hand back the computer'}]


def test_failed_teams_and_zoom_joins_never_keep_another_tab():
    from cognitrix.meetings import live

    address = {'value': 'https://mail.google.com/mail/u/0/#drafts'}

    class Driver:
        def look(self, window_id, query=None, limit=60, viewer=None):
            elements = [SimpleNamespace(role='entry', name='Address and search bar', value=address['value'],
                                        enabled=True, rect=(0, 0, 1, 1))]
            return 'IMAGE', SimpleNamespace(id='s1', elements=elements, origin=(0, 0), scale=1.0), [], ''

    for platform, link, sign_in in (
            ('teams', 'https://teams.microsoft.com/l/meetup-join/abc/0', 'https://login.microsoftonline.com/x'),
            ('zoom', 'https://us02web.zoom.us/wc/join/81234567890', 'https://us02web.zoom.us/signin')):
        screen = live.Screen(Driver(), 'meeting:1')
        screen.platform, screen.url, screen.window_id = platform, link, 5
        address['value'] = 'https://mail.google.com/mail/u/0/#drafts'
        screen.look()
        assert screen.last_view is None and screen.last_image is None, platform
        address['value'] = sign_in
        screen.look()
        assert screen.last_image == 'IMAGE', platform


async def test_the_relational_upgrade_fills_in_an_empty_runner():
    from cognitrix.config import _MEETING_RELATIONAL_COLUMNS, _migrate_relational_task_schema

    statements = []

    class Cursor:
        def fetchall(self):
            return []

    class Database:
        dbms = 'postgresql'

        @staticmethod
        async def query(statement, params=None):
            statements.append(statement)
            if 'pg_index' in statement.lower():
                raise RuntimeError('stop here')
            return Cursor()

    try:
        await _migrate_relational_task_schema(Database)
    except RuntimeError:
        pass
    assert ('call_state', 'TEXT') in _MEETING_RELATIONAL_COLUMNS
    assert "UPDATE meetingnotes SET runner = '' WHERE runner IS NULL" in statements


def test_the_recorder_says_which_recorder_it_is_and_its_clock():
    from cognitrix.computer.sandbox import audio

    desk = audio.DesktopAudio.__new__(audio.DesktopAudio)
    rec = audio.Recorder('x.monitor')
    desk.recorder = rec
    read = desk.read(0, 0)
    assert read['recorder'] == rec.id and abs(read['now'] - __import__('time').time()) < 5
