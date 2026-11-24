"""Chat turns that were running when the engine stopped carry on afterwards."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from cognitrix.sessions import resume
from cognitrix.sessions import runs as chat_runs


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'resume.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path / 'data')
    (tmp_path / 'data').mkdir()
    chat_runs._RUNS.clear()
    resume._TRACKED.clear()
    monkeypatch.setattr(resume, '_SHUTTING_DOWN', False)
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    yield tmp_path
    chat_runs._RUNS.clear()
    resume._TRACKED.clear()


AGENT = SimpleNamespace(id='a1', name='Helper')


@pytest.fixture
def world(monkeypatch):
    """A fake agent; a turn records what it was given and answers."""
    import cognitrix.agents.access as access
    from cognitrix.sessions.base import Session

    state = SimpleNamespace(turns=[], gate=None)

    async def find(user_key, *, agent_id=None, name=None):
        return AGENT if agent_id == AGENT.id else None

    async def fake_call(self, message, agent, *args, **kwargs):
        state.turns.append((str(self.id), message, [dict(m) for m in self.chat]))
        if state.gate is not None:
            await state.gate.wait()
        self.chat = [*self.chat, {'role': 'assistant', 'type': 'text', 'content': 'all done'},
                     {'role': 'system', 'type': 'turn_timing', 'content': 'Took 1s'}]
        await Session.update_one({'id': str(self.id)}, {'chat': self.chat})

    monkeypatch.setattr(access, 'find_visible_agent', find)
    monkeypatch.setattr(Session, '__call__', fake_call)
    return state


TOOL_CALL = {'role': 'assistant', 'type': 'tool_calls', 'content': '',
             'tool_calls': [{'name': 'Search', 'arguments': {}, 'tool_call_id': 't1'},
                            {'name': 'Fetch', 'arguments': {}, 'tool_call_id': 't2'}]}


async def _session(chat, user='u1'):
    from cognitrix.sessions.base import Session

    session = Session(agent_id=AGENT.id, user_id=user, chat=chat)
    await session.save()
    return session


async def _lease(session, *, owner='old-engine', beat_ago=600, resumes=0, kind='web', started_ago=60):
    now = datetime.now(UTC)
    lease = resume.ChatTurnLease(
        user_id=str(session.user_id), agent_id=AGENT.id, session_id=str(session.id), run_id='r-old',
        kind=kind, owner=owner, heartbeat_at=resume._iso(now - timedelta(seconds=beat_ago)),
        started_at=resume._iso(now - timedelta(seconds=started_ago)), resumes=resumes, prompt='find me a flat',
    )
    await lease.save()
    return lease


async def _settle():
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not resume._TASKS and not any(r.running for r in chat_runs._RUNS.values()):
            break
    if resume._TASKS:
        await asyncio.gather(*list(resume._TASKS), return_exceptions=True)


def test_mid_turn_and_open_tool_calls():
    asked = {'role': 'User', 'type': 'text', 'content': 'hi'}
    assert resume.mid_turn([asked])
    assert resume.mid_turn([asked, TOOL_CALL])
    assert resume.mid_turn([asked, TOOL_CALL, {'role': 'tool', 'tool_call_id': 't1', 'content': 'x'}])
    assert not resume.mid_turn([asked, {'role': 'assistant', 'type': 'text', 'content': 'done'}])
    assert not resume.mid_turn([asked, {'role': 'system', 'type': 'turn_timing', 'content': 'Took 1s'}])
    assert not resume.mid_turn([])

    closed = resume.close_open_tool_calls([asked, TOOL_CALL, {'role': 'tool', 'tool_call_id': 't1', 'content': 'x'}])
    assert [c['tool_call_id'] for c in closed] == ['t2']
    assert closed[0]['outcome']['status'] == 'stopped'
    assert resume.close_open_tool_calls([asked]) == []


async def test_a_tracked_turn_has_a_lease_until_it_ends(db):
    run = chat_runs.start_run('u1', AGENT.id, 's1', prompt='hello')
    resume.track(run, 'web', prompt='hello')
    await _settle()
    leases = await resume.ChatTurnLease.find({'session_id': 's1'})
    assert [(lease.status, lease.owner, lease.prompt) for lease in leases] == [('running', resume.OWNER, 'hello')]

    run.finish({'type': 'turn_complete', 'content': '', 'session_id': 's1'})
    await _settle()
    assert (await resume.ChatTurnLease.find({'session_id': 's1'}))[0].status == 'ended'


async def test_a_turn_cut_off_by_shutdown_keeps_its_lease(db, monkeypatch):
    run = chat_runs.start_run('u1', AGENT.id, 's2')
    resume.track(run, 'web')
    await _settle()
    resume.shutting_down()
    run.finish({'type': 'turn_stopped', 'content': '', 'session_id': 's2'})
    await _settle()
    assert (await resume.ChatTurnLease.find({'session_id': 's2'}))[0].status == 'running'


async def test_swarm_workers_and_unknown_kinds_are_not_tracked(db):
    run = chat_runs.start_run('u1', AGENT.id, 's3')
    run.swarm_id = 'sw'
    resume.track(run, 'web')
    resume.track(chat_runs.start_run('u1', AGENT.id, 's4'), 'team')
    await _settle()
    assert await resume.ChatTurnLease.find({}) == []


async def test_an_interrupted_turn_is_picked_up_where_it_stopped(db, world):
    from cognitrix.sessions.base import Session

    session = await _session([{'role': 'User', 'type': 'text', 'content': 'find me a flat'}, TOOL_CALL,
                              {'role': 'tool', 'tool_call_id': 't1', 'content': 'three flats'}])
    old = await _lease(session)

    assert await resume.recover_pass() == 1
    await _settle()

    sid, message, chat = world.turns[0]
    assert sid == str(session.id)
    assert isinstance(message, resume.ContinueTurn) and message.goal == 'find me a flat'
    # The running tool got an "interrupted" result and the turn a note.
    assert chat[-2]['tool_call_id'] == 't2' and 'Interrupted' in chat[-2]['content']
    assert chat[-1]['type'] == 'steer' and chat[-1]['resumed'] is True
    saved = await Session.get(str(session.id))
    assert saved.chat[-2]['content'] == 'all done'

    leases = {lease.id: lease for lease in await resume.ChatTurnLease.find({'session_id': str(session.id)})}
    assert leases[old.id].status == 'resumed'
    new = [lease for lease in leases.values() if lease.id != old.id]
    assert len(new) == 1 and new[0].resumes == 1 and new[0].status == 'ended'
    assert new[0].prompt == 'find me a flat' and new[0].started_at == leases[old.id].started_at


async def test_a_turn_is_only_picked_up_once_across_engines(db, world):
    world.gate = asyncio.Event()
    session = await _session([{'role': 'User', 'type': 'text', 'content': 'go'}])
    lease = await _lease(session)
    first = await resume.ChatTurnLease.get(lease.id)
    second = await resume.ChatTurnLease.get(lease.id)

    assert await resume._claim(first)
    assert not await resume._claim(second)
    world.gate.set()


async def test_live_and_finished_turns_are_left_alone(db, world):
    fresh = await _session([{'role': 'User', 'type': 'text', 'content': 'go'}])
    await _lease(fresh, beat_ago=5)
    done = await _session([{'role': 'User', 'type': 'text', 'content': 'go'},
                           {'role': 'assistant', 'type': 'text', 'content': 'answer'}])
    done_lease = await _lease(done)

    assert await resume.recover_pass() == 0
    await _settle()
    assert world.turns == []
    assert (await resume.ChatTurnLease.get(done_lease.id)).status == 'ended'


async def test_the_desktop_engine_picks_up_its_previous_run_at_once(db, world, monkeypatch):
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    session = await _session([{'role': 'User', 'type': 'text', 'content': 'go'}])
    await _lease(session, beat_ago=1)
    assert await resume.recover_pass() == 1
    await _settle()
    assert len(world.turns) == 1


async def test_a_turn_that_keeps_crashing_is_dropped_with_an_alert(db, world):
    from cognitrix.tasks.automation import AgentNotification

    session = await _session([{'role': 'User', 'type': 'text', 'content': 'go'}])
    lease = await _lease(session, resumes=resume.MAX_RESUMES)
    old = await _session([{'role': 'User', 'type': 'text', 'content': 'go'}])
    old_lease = await _lease(old, started_ago=resume.MAX_AGE_SECONDS + 60)

    assert await resume.recover_pass() == 0
    assert world.turns == []
    assert (await resume.ChatTurnLease.get(lease.id)).status == 'dropped'
    assert (await resume.ChatTurnLease.get(old_lease.id)).status == 'dropped'
    alerts = await AgentNotification.find({'user_id': 'u1'})
    assert len(alerts) == 2 and all('interrupted' in a.title for a in alerts)


async def test_turned_off_in_settings(db, world, monkeypatch):
    from cognitrix import app_settings

    monkeypatch.setitem(app_settings._overrides, 'COGNITRIX_RESUME_CHATS', '0')
    session = await _session([{'role': 'User', 'type': 'text', 'content': 'go'}])
    await _lease(session)
    assert await resume.recover_pass() == 0
    run = chat_runs.start_run('u1', AGENT.id, 'x')
    resume.track(run, 'web')
    await _settle()
    assert len(await resume.ChatTurnLease.find({})) == 1


async def test_continue_turn_adds_no_message_and_saves_each_round(monkeypatch):
    """The real turn loop: a picked-up turn answers from the saved history
    without a new request, and a web turn is saved after every tool round."""
    from cognitrix.sessions.base import Session
    from tests.test_steering import _agent, _patch

    saves = []
    probe, prompts = _patch(monkeypatch, ['tool', 'finished'])

    async def counting_save(self):
        saves.append(len(self.chat))
        return self

    monkeypatch.setattr(Session, 'save', counting_save)
    session = Session(agent_id='a1', chat=[
        {'role': 'User', 'type': 'text', 'content': 'find me a flat'},
        {'role': 'User', 'type': 'steer', 'content': resume.RESUME_NOTE, 'resumed': True},
    ])
    session.id = 's9'

    async def sink(*_a, **_k):
        return None

    await session(resume.ContinueTurn(goal='find me a flat'), _agent([probe]), 'web', False, sink,
                  {'type': 'generate', 'action': 'chat_message'}, True)

    users = [m for m in session.chat if str(m.get('role')).lower() == 'user']
    assert [m['content'] for m in users][0] == 'find me a flat' and len(users) == 2
    assert session.chat[-2]['content'] == 'finished'
    # Saved after the tool round (history then ends with the tool result) and at the end.
    assert any(session.chat[n - 1].get('role') == 'tool' for n in saves if n <= len(session.chat))
    assert saves[-1] == len(session.chat)
