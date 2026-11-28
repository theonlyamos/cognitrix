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
