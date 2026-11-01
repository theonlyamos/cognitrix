"""Team group chats: who answers, what each agent sees, and handoffs."""

from dataclasses import dataclass, field, replace

import pytest

from cognitrix.teams import group_chat as gc


@dataclass
class FakeAgent:
    id: str
    name: str
    system_prompt: str = ''
    description: str = ''

    def model_copy(self, update=None):
        return replace(self, **(update or {}))


@dataclass
class FakeTeam:
    name: str = 'Crew'
    leader_id: str | None = None


@dataclass
class FakeSession:
    id: str = 's1'
    team_id: str = 't1'
    user_id: str = 'u1'
    chat: list = field(default_factory=list)
    saves: int = 0

    def update_history(self, entry):
        self.chat.append(entry)

    async def save(self):
        self.saves += 1


ALICE = FakeAgent('a', 'Alice')
BOB = FakeAgent('b', 'Bob Smith')
CARA = FakeAgent('c', 'Cara')
TEAM = [ALICE, BOB, CARA]


def test_mentions_in_order_with_names_handles_and_everyone():
    assert gc.find_mentions('@cara then @Alice', TEAM) == ['c', 'a']
    assert gc.find_mentions('ask @Bob Smith and @bob-smith', TEAM) == ['b']
    assert gc.find_mentions('ping @Bob_Smith.', TEAM) == ['b']
    assert gc.find_mentions('@all please', TEAM) == ['a', 'b', 'c']
    assert gc.find_mentions('@Cara, then @everyone', TEAM) == ['c', 'a', 'b']


def test_mentions_ignore_emails_code_and_partial_names():
    assert gc.find_mentions('mail me at x@alice.com', TEAM) == []
    assert gc.find_mentions('run `@alice` literally', TEAM) == []
    assert gc.find_mentions('@alicex is not alice', TEAM) == []
    assert gc.find_mentions('@allison', TEAM) == []


def test_first_responders_fall_back_to_last_speaker_then_leader():
    assert gc.first_responders('hi', TEAM, [], None) == ['a']
    assert gc.first_responders('hi', TEAM, [], 'c') == ['c']
    chat = [{'speaker_id': 'user'}, {'speaker_id': 'b'}, {'speaker_id': 'user'}]
    assert gc.first_responders('and then?', TEAM, chat, 'c') == ['b']
    assert gc.first_responders('@alice you', TEAM, chat, 'c') == ['a']


def test_agent_view_keeps_own_turns_and_folds_others():
    chat = [
        gc.transcript_entry('Plan a trip'),
        {'role': 'assistant', 'type': 'tool_calls', 'content': '', 'speaker_id': 'a',
         'tool_calls': [{'name': 'search', 'tool_call_id': 't1'}]},
        {'role': 'tool', 'tool_call_id': 't1', 'content': 'results', 'speaker_id': 'a'},
        {'role': 'assistant', 'type': 'text', 'content': 'Here is a plan. @Bob Smith check costs',
         'speaker_id': 'a', 'speaker': 'Alice'},
        {'role': 'system', 'type': 'turn_timing', 'content': 'Took 1s', 'speaker_id': 'a'},
        {'role': 'assistant', 'type': 'text', 'content': '<think>hmm</think>About $900.',
         'speaker_id': 'b', 'speaker': 'Bob Smith'},
    ]
    history, trigger = gc.agent_view(chat[:-1], 'b')
    assert history == []
    assert trigger == ('User: Plan a trip\n\n'
                       'Alice (teammate): Here is a plan. @Bob Smith check costs')

    history, trigger = gc.agent_view(chat, 'a')
    assert history[0] == {'role': 'User', 'type': 'text', 'content': 'User: Plan a trip'}
    assert [m['role'] for m in history[1:]] == ['assistant', 'tool', 'assistant', 'system']
    assert all('speaker_id' not in m for m in history)
    assert trigger == 'Bob Smith (teammate): About $900.'


def test_system_prompt_lists_teammates_and_lead_role():
    text = gc.group_system_prompt(ALICE, FakeTeam(leader_id='a'), TEAM)
    assert '@Bob-Smith' in text and '@Cara' in text
    assert 'You are the team lead' in text
    assert '@Alice (team lead)' not in text


@pytest.mark.asyncio
async def test_handoffs_follow_mentions_and_stop_at_limits(monkeypatch):
    replies = {
        'a': ['@Bob Smith can you price it?', 'Thanks @Bob Smith'],
        'b': ['About $900. @Alice @Cara FYI', 'Again @Alice'],
        'c': ['Noted.'],
    }
    seen: dict[str, list[str]] = {}

    async def run_agent(agent, scratch, trigger, emit):
        seen.setdefault(agent.id, []).append(trigger)
        assert 'Group chat' in agent.system_prompt
        scratch.update_history({'role': 'User', 'type': 'text', 'content': trigger})
        scratch.update_history({'role': 'assistant', 'type': 'text',
                                'content': replies[agent.id].pop(0)})

    events = []

    async def emit(event):
        events.append(event)

    session = FakeSession()
    session.update_history(gc.transcript_entry('@alice plan it'))
    spoken = await gc.run_group_turn(
        session, FakeTeam(), TEAM, '@alice plan it', emit=emit, run_agent=run_agent,
    )
    # Alice -> Bob -> (Alice, Cara) -> Bob again -> Alice hits her limit.
    assert spoken == ['a', 'b', 'a', 'c', 'b']
    assert [e['name'] for e in events if e['type'] == 'group_speaker'] == [
        'Alice', 'Bob Smith', 'Alice', 'Cara', 'Bob Smith',
    ]
    speakers = [e['speaker_id'] for e in session.chat]
    assert speakers == ['user', 'a', 'b', 'a', 'c', 'b']
    # Cara never spoke, so she sees the whole conversation as one message.
    assert seen['c'] == ['User: @alice plan it\n\n'
                         'Alice (teammate): @Bob Smith can you price it?\n\n'
                         'Bob Smith (teammate): About $900. @Alice @Cara FYI\n\n'
                         'Alice (teammate): Thanks @Bob Smith']
    # Bob's second turn sees only what happened since he last spoke.
    assert seen['b'][1] == 'Alice (teammate): Thanks @Bob Smith\n\nCara (teammate): Noted.'
    assert session.saves == 5


@pytest.mark.asyncio
async def test_turn_cap_pauses_long_handoff_chains(monkeypatch):
    monkeypatch.setattr(gc, 'MAX_AGENT_TURNS', 2)

    async def run_agent(agent, scratch, trigger, emit):
        scratch.update_history({'role': 'User', 'type': 'text', 'content': trigger})
        scratch.update_history({'role': 'assistant', 'type': 'text', 'content': '@all over to you'})

    events = []

    async def emit(event):
        events.append(event)

    session = FakeSession()
    session.update_history(gc.transcript_entry('go'))
    spoken = await gc.run_group_turn(session, FakeTeam(), TEAM, 'go', emit=emit, run_agent=run_agent)
    assert spoken == ['a', 'b']
    assert events[-1]['type'] == 'status' and 'Paused' in events[-1]['content']


@pytest.mark.asyncio
async def test_stopped_agent_keeps_partial_output():
    import asyncio

    async def run_agent(agent, scratch, trigger, emit):
        scratch.update_history({'role': 'User', 'type': 'text', 'content': trigger})
        scratch.update_history({'role': 'assistant', 'type': 'text', 'content': 'partial'})
        raise asyncio.CancelledError

    async def emit(event):
        pass

    session = FakeSession()
    session.update_history(gc.transcript_entry('hi'))
    with pytest.raises(asyncio.CancelledError):
        await gc.run_group_turn(session, FakeTeam(), TEAM, 'hi', emit=emit, run_agent=run_agent)
    assert session.chat[-1]['content'] == 'partial'
    assert session.chat[-1]['speaker'] == 'Alice'
    assert session.saves == 1


def test_mention_scan_stays_fast_on_hostile_input():
    import time

    hostile = '<think>' * 20000 + '```' * 20000 + '`@alice' * 5000
    started = time.monotonic()
    gc.find_mentions(hostile, TEAM)
    gc.visible_text({'role': 'assistant', 'type': 'text', 'content': hostile})
    assert time.monotonic() - started < 2
    assert gc.find_mentions('<think>@bob</think> @cara', TEAM) == ['c']


def test_agents_saved_before_folder_columns_still_load():
    from cognitrix.models.agent import Agent

    assert Agent._can_request_folders_default(None) is True
    assert Agent._can_request_folders_default(False) is False
