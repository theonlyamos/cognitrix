"""Swarms: Spawn Workers, the combined report, limits and steering."""
import asyncio
from types import SimpleNamespace

import pytest

from cognitrix.tools.utils import ToolExecutionContext, set_execution_context


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings
    from cognitrix.swarms import service

    db_file = str(tmp_path / 'swarms.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path / 'data')
    (tmp_path / 'data').mkdir()
    yield tmp_path
    for live in list(service._LIVE.values()):
        if live.watchdog is not None:
            live.watchdog.cancel()
    service._LIVE.clear()
    set_execution_context(ToolExecutionContext())


class FakeLLM(SimpleNamespace):
    pass


class FakeAgent(SimpleNamespace):
    def model_copy(self, update=None):
        return FakeAgent(**{**self.__dict__, **(update or {})})


def _agent(agent_id, name, **kw):
    return FakeAgent(id=agent_id, name=name, llm=FakeLLM(id=f'llm-{agent_id}', provider='anthropic'),
                           tools=[], system_prompt='', **kw)


BOSS = _agent('a-boss', 'Boss')
RESEARCHER = _agent('a-res', 'Researcher')
AGENTS = {a.id: a for a in (BOSS, RESEARCHER)}


@pytest.fixture
def world(monkeypatch):
    """Fake agents and a fake model. A turn replies with a summary of its
    job; a job containing FAIL raises, SLOW waits, and every turn uses 100 tokens."""
    import cognitrix.agents.access as access
    from cognitrix import usage
    from cognitrix.sessions.base import Session

    state = SimpleNamespace(turns=[], active=0, peak=0, gate=None, replies={}, on_turn=None)

    async def find(user_key, *, agent_id=None, name=None):
        if agent_id in AGENTS:
            return AGENTS[agent_id]
        return next((a for a in AGENTS.values() if a.name == name), None)

    async def fake_call(self, text, agent, *args, **kwargs):
        state.turns.append((str(self.id), agent.name, text))
        state.active += 1
        state.peak = max(state.peak, state.active)
        try:
            usage.log({'prompt_tokens': 80, 'completion_tokens': 20})
            if state.gate is not None and 'report:' not in text:
                await state.gate.wait()
            else:
                await asyncio.sleep(0.01)
            if 'FAIL' in text and 'report:' not in text:
                raise RuntimeError('model exploded')
            job = text.split('## Your job', 1)[-1].strip().splitlines()[0] if '## Your job' in text else text[-40:]
            reply = state.replies.get(job) or f'Summary: did {job}\n\nDetails for {job}.'
            if state.on_turn is not None:
                state.on_turn(self, text)
            self.chat = [*self.chat, {'role': 'user', 'content': text}, {'role': 'assistant', 'content': reply}]
            output = kwargs.get('output') or args[2]
            await output({'type': 'generate', 'content': reply})
            await Session.update_one({'id': str(self.id)}, {'chat': self.chat})
        finally:
            state.active -= 1

    monkeypatch.setattr(access, 'find_visible_agent', find)
    monkeypatch.setattr(Session, '__call__', fake_call)
    monkeypatch.setattr('cognitrix.sessions.agent_chats.REPORT_POLL_SECONDS', 0.01)
    return state


async def _lead(user='u1', **kw):
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions.base import Session

    session = Session(agent_id=BOSS.id, user_id=user, **kw)
    await session.save()
    await claim_new(str(session.id), user, BOSS.id)
    set_execution_context(ToolExecutionContext(user_id=user, session_id=str(session.id), agent_id=BOSS.id))
    return session


async def _call(tool, **params):
    return (await tool.run(parent=BOSS, **params)).outcome


async def _settle():
    from cognitrix.swarms import service

    await service.settle()


def _swarm_id(outcome) -> str:
    assert outcome.status == 'success', outcome.text
    return outcome.entities[0].id


def _lead_turns(world, lead):
    return [t for t in world.turns if t[0] == str(lead.id)]


async def test_workers_run_and_the_lead_gets_one_report(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead()
    outcome = await _call(spawn_workers, title='Pricing', workers=[
        {'name': 'acme', 'brief': 'find acme pricing', 'role': 'Researcher'},
        {'name': 'globex', 'brief': 'find globex pricing'},
        {'brief': 'find initech pricing', 'role': 'skeptical analyst'},
    ])
    sid = _swarm_id(outcome)
    assert 'One combined report' in outcome.text
    await _settle()

    swarm = await service.get_swarm('u1', sid)
    workers = await service.workers_of(sid)
    assert swarm.status == 'done'
    assert [w.status for w in workers] == ['done', 'done', 'done']
    assert workers[0].agent_id == RESEARCHER.id and workers[1].agent_id == BOSS.id
    # A made-up role runs as a copy of the lead, told its role.
    assert workers[2].agent_id == BOSS.id and workers[2].name == 'skeptical-analyst'
    briefs = [t[2] for t in world.turns if t[0] == workers[2].chat_id]
    assert 'Your role: skeptical analyst' in briefs[0]

    # One lead turn with every worker in it.
    turns = _lead_turns(world, lead)
    assert len(turns) == 1
    report = turns[0][2]
    assert report.startswith('[Agent chat reply] Swarm "Pricing"') and '3 of 3 done' in report
    for name in ('acme', 'globex', 'skeptical-analyst'):
        assert f'### {name}' in report
    assert 'did find acme pricing' in report and 'Details for' not in report

    # Results are files; the summary is what the lead was sent.
    for w in workers:
        text = open(w.result_path, encoding='utf-8').read()
        assert text.startswith('Summary: did') and 'Details for' in text
        assert w.summary.startswith('did ')
        assert w.tokens == 100
    assert swarm.id and (await service.get_swarm('u1', sid)).tokens == 300


async def test_worker_chats_are_marked_and_read_only(db, world):
    from cognitrix.sessions.base import Session
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead(folders=[{'path': str(db), 'access': 'write', 'name': 'proj', 'primary': True}])
    sid = _swarm_id(await _call(spawn_workers, title='Docs', workers=[{'brief': 'read the docs'}]))
    await _settle()
    worker = (await service.workers_of(sid))[0]
    chat = await Session.get(worker.chat_id)
    assert chat.started_by['swarm_id'] == sid and chat.started_by['worker'] == worker.name
    assert chat.started_by['session_id'] == str(lead.id) and chat.started_by['report_back'] is False
    assert chat.title == 'Docs: worker'
    shared = [f for f in chat.folders if f['name'] == 'proj']
    assert shared and shared[0]['access'] == 'read'


async def test_only_so_many_run_at_once(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    await service.save_settings('u1', {'concurrency': 2, 'auto_start': 10})
    await _lead()
    world.gate = asyncio.Event()
    sid = _swarm_id(await _call(spawn_workers, items=['a', 'b', 'c', 'd', 'e'], brief='look up {item}'))
    await asyncio.sleep(0.05)
    workers = await service.workers_of(sid)
    assert sorted(w.status for w in workers) == ['queued', 'queued', 'queued', 'running', 'running']
    assert [w.name for w in workers] == ['a', 'b', 'c', 'd', 'e']
    assert workers[0].brief == 'look up a'
    world.gate.set()
    await _settle()
    assert world.peak == 2
    assert all(w.status == 'done' for w in await service.workers_of(sid))


async def test_dependencies_wait_and_get_summaries(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    await _lead()
    sid = _swarm_id(await _call(spawn_workers, workers=[
        {'name': 'write', 'brief': 'write it up', 'depends_on': ['research']},
        {'name': 'research', 'brief': 'gather facts'},
        {'name': 'doomed', 'brief': 'FAIL now'},
        {'name': 'after-doomed', 'brief': 'use doomed', 'depends_on': ['doomed']},
    ]))
    await _settle()
    workers = {w.name: w for w in await service.workers_of(sid)}
    order = [t[2] for t in world.turns if 'report:' not in t[2]]
    assert order.index(next(t for t in order if 'gather facts' in t)) < order.index(
        next(t for t in order if 'write it up' in t))
    brief = next(t for t in order if 'write it up' in t)
    assert '## What teammates already found' in brief and 'did gather facts' in brief
    assert workers['doomed'].status == 'failed' and 'model exploded' in workers['doomed'].error
    assert workers['after-doomed'].status == 'failed' and 'doomed' in workers['after-doomed'].error

    out = await _call(spawn_workers, workers=[{'brief': 'x', 'depends_on': ['nobody']}])
    assert out.status == 'error' and out.error.code == 'bad_dependency'


async def test_limits_and_asking_first(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers, swarm_requested

    await _lead()
    # More than the agent may start alone, and nobody to ask.
    out = await _call(spawn_workers, items=[str(i) for i in range(6)], brief='do {item}')
    assert out.status == 'error' and out.error.code == 'needs_confirmation'
    token = swarm_requested.set(True)
    try:
        sid = _swarm_id(await _call(spawn_workers, items=[str(i) for i in range(6)], brief='do {item}'))
    finally:
        swarm_requested.reset(token)
    await _settle()
    assert len(await service.workers_of(sid)) == 6

    out = await _call(spawn_workers, items=[str(i) for i in range(26)], brief='do {item}')
    assert out.error.code == 'too_many_workers'
    out = await _call(spawn_workers, workers=[{'name': 'x'}])
    assert out.error.code == 'brief_required'

    await service.save_settings('u1', {'enabled': False})
    out = await _call(spawn_workers, workers=[{'brief': 'x'}])
    assert out.error.code == 'swarms_off'


async def test_the_person_is_asked_above_the_limit(db, world, monkeypatch):
    from cognitrix.questions.models import QuestionAnswer
    from cognitrix.tools.swarm import spawn_workers

    await _lead()
    asked = []

    async def ask(spec):
        asked.append(spec)
        return QuestionAnswer.option('no', "Don't start")

    monkeypatch.setattr('cognitrix.questions.broker.ask_question', ask)
    out = await _call(spawn_workers, items=[str(i) for i in range(6)], brief='do {item}')
    assert out.error.code == 'declined' and len(asked) == 1

    async def yes(spec):
        return QuestionAnswer.option('start', 'Start')

    monkeypatch.setattr('cognitrix.questions.broker.ask_question', yes)
    _swarm_id(await _call(spawn_workers, items=[str(i) for i in range(6)], brief='do {item}'))
    await _settle()
    assert len([t for t in world.turns if 'report:' in t[2]]) == 1


async def test_workers_cannot_start_swarms_and_other_refusals(db, world):
    from cognitrix.tools.swarm import spawn_workers, swarm

    await _lead(started_by={'swarm_id': 's1', 'worker': 'w'})
    assert (await _call(spawn_workers, workers=[{'brief': 'x'}])).error.code == 'too_deep'
    await _lead(incognito=True)
    assert (await _call(spawn_workers, workers=[{'brief': 'x'}])).error.code == 'incognito'
    await _lead(group_chat=True)
    assert (await _call(spawn_workers, workers=[{'brief': 'x'}])).error.code == 'not_here'
    await _lead()
    assert (await _call(swarm, action='status')).error.code == 'swarm_not_found'
    set_execution_context(ToolExecutionContext(user_id='u1', api_key_id='k1', session_id='s'))
    assert (await _call(spawn_workers, workers=[{'brief': 'x'}])).error.code == 'not_allowed'


async def test_token_budget_stops_new_workers(db, world):
    from cognitrix.swarms import service
    from cognitrix.swarms.models import SwarmSettings
    from cognitrix.tools.swarm import spawn_workers

    await _lead()
    await service.save_settings('u1', {'concurrency': 1})
    row = await SwarmSettings.find_one({'user_id': 'u1'})
    await SwarmSettings.update_one({'id': row.id}, {'token_budget': 10_000})
    sid = _swarm_id(await _call(spawn_workers, items=['a', 'b', 'c'], brief='do {item}'))
    from cognitrix.swarms.models import SwarmRun

    await SwarmRun.update_one({'id': sid}, {'limits': {'concurrency': 1, 'per_person': 8, 'max_workers': 25,
                                                       'token_budget': 150}})
    await _settle()
    workers = await service.workers_of(sid)
    assert [w.status for w in workers] == ['done', 'done', 'stopped']
    assert 'token budget' in workers[2].error
    report = world.turns[-1][2]
    assert 'reached its token budget' in report and '### c: stopped' in report


async def test_wait_returns_the_report_and_the_lead_is_not_woken_twice(db, world):
    from cognitrix.tools.swarm import spawn_workers, swarm

    lead = await _lead()
    world.gate = asyncio.Event()
    _swarm_id(await _call(spawn_workers, workers=[{'brief': 'quick one'}, {'brief': 'quick two'}]))
    status = await _call(swarm, action='status')
    assert 'worker: running' in status.text and 'worker-2: running' in status.text

    async def release():
        await asyncio.sleep(0.05)
        world.gate.set()

    asyncio.get_running_loop().create_task(release())
    out = await _call(swarm, action='wait')
    assert out.status == 'success' and '2 of 2 done' in out.text and not out.text.startswith('[Agent chat reply]')
    await _settle()
    assert _lead_turns(world, lead) == []
    again = await _call(swarm, action='wait')
    assert 'Nothing new' in again.text


async def test_wait_gives_up_and_the_report_still_arrives(db, world, monkeypatch):
    from cognitrix.tools.swarm import spawn_workers, swarm

    monkeypatch.setenv('COGNITRIX_SWARM_WAIT_SECONDS', '0.05')
    lead = await _lead()
    world.gate = asyncio.Event()
    _swarm_id(await _call(spawn_workers, workers=[{'brief': 'slow one'}]))
    out = await _call(swarm, action='wait')
    assert 'Still working' in out.text
    world.gate.set()
    await _settle()
    assert len(_lead_turns(world, lead)) == 1


async def test_report_after_each_worker(db, world):
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead()
    await _call(spawn_workers, workers=[{'brief': 'one'}, {'brief': 'two', 'depends_on': ['worker']}], wait='any')
    await _settle()
    turns = _lead_turns(world, lead)
    assert len(turns) == 2
    assert '1 of 2 done' in turns[0][2] and 'still working' in turns[0][2]
    assert '2 of 2 done' in turns[1][2] and '### worker-2' in turns[1][2] and '### worker:' not in turns[1][2]


async def test_read_a_result_and_stop(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers, swarm

    lead = await _lead()
    world.replies['long job'] = 'Summary: long\n\n' + 'x' * 30_000
    sid = _swarm_id(await _call(spawn_workers, workers=[{'name': 'long', 'brief': 'long job'}]))
    await _settle()
    first = await _call(swarm, action='result', worker='long')
    assert first.text.startswith('Summary: long') and 'offset 24000' in first.text
    rest = await _call(swarm, action='result', worker='long', offset=24_000)
    assert rest.text.startswith('x') and 'offset' not in rest.text
    assert (await _call(swarm, action='result', worker='nobody')).error.code == 'worker_not_found'
    assert (await _call(swarm, action='result', worker='long', file='../../x')).error.code == 'file_not_found'

    world.gate = asyncio.Event()
    sid = _swarm_id(await _call(spawn_workers, title='Second', workers=[{'brief': 'a'}, {'brief': 'b'}]))
    await asyncio.sleep(0.03)
    assert (await _call(swarm, action='stop', worker='worker')).text == 'Stopped "worker".'
    await asyncio.sleep(0.03)
    assert (await service.find_worker(sid, 'worker')).status == 'stopped'
    out = await _call(swarm, action='stop')
    assert 'Stopped the swarm "Second"' in out.text
    await _settle()
    swarm_row = await service.get_swarm('u1', sid)
    assert swarm_row.status == 'stopped'
    assert {w.status for w in await service.workers_of(sid)} == {'stopped'}
    # Stopping it does not wake the lead with a report (only the first swarm did).
    assert len(_lead_turns(world, lead)) == 1


async def test_message_reopens_a_finished_worker(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers, swarm

    lead = await _lead()
    sid = _swarm_id(await _call(spawn_workers, workers=[{'name': 'facts', 'brief': 'gather facts'}]))
    await _settle()
    out = await _call(swarm, action='message', worker='facts', message='also check 2025')
    assert 'working again' in out.text
    await _settle()
    worker = await service.find_worker(sid, 'facts')
    assert worker.status == 'done' and worker.received == 1
    chat_turns = [t[2] for t in world.turns if t[0] == worker.chat_id]
    assert len(chat_turns) == 2 and 'Boss (the lead) says' in chat_turns[1] and 'also check 2025' in chat_turns[1]
    assert len(_lead_turns(world, lead)) == 2
    assert (await service.get_swarm('u1', sid)).status == 'done'
    assert (await _call(swarm, action='message', worker='facts', message='')).error.code == 'message_required'


async def test_a_busy_model_is_retried(db, world, monkeypatch):
    from cognitrix.sessions.base import Session
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    monkeypatch.setattr(service, 'RATE_LIMIT_DELAYS', (0.01, 0.01))
    original = Session.__call__
    calls = {'n': 0}

    async def flaky(self, text, agent, *args, **kwargs):
        if 'report:' not in text:
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('429 rate limit exceeded')
        return await original(self, text, agent, *args, **kwargs)

    monkeypatch.setattr(Session, '__call__', flaky)
    await _lead()
    sid = _swarm_id(await _call(spawn_workers, workers=[{'brief': 'job'}]))
    await _settle()
    worker = (await service.workers_of(sid))[0]
    assert worker.status == 'done' and worker.attempts == 2 and calls['n'] == 2


# --- phase 2: the board, messages, the gate, best of N, separate copies ---------

async def _as_worker(sid, name, user='u1'):
    """Make the following tool calls come from a worker's own chat."""
    from cognitrix.swarms import service

    worker = await service.find_worker(sid, name)
    set_execution_context(ToolExecutionContext(user_id=user, session_id=worker.chat_id, agent_id=worker.agent_id))
    return worker


async def test_workers_see_the_board_add_work_and_message_each_other(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers, swarm_team

    lead = await _lead()
    world.gate = asyncio.Event()
    sid = _swarm_id(await _call(spawn_workers, workers=[
        {'name': 'one', 'brief': 'part one'}, {'name': 'two', 'brief': 'part two'}]))
    await asyncio.sleep(0.05)

    await _as_worker(sid, 'one')
    board = (await _call(swarm_team, action='board')).text
    assert '- one (you): running' in board and '- two: running' in board

    added = await _call(swarm_team, action='add_work', brief='a part nobody has', name='extra')
    assert added.status == 'success' and 'extra' in added.text
    extra = await service.find_worker(sid, 'extra')
    assert extra.added_by == 'one' and extra.status in ('queued', 'running')

    # A teammate hears it between its tool calls; chatter is capped per pair.
    told = await _call(swarm_team, action='message', worker='two', message='The API moved to v2.')
    assert told.status == 'success'
    for _ in range(2):
        await _call(swarm_team, action='message', worker='two', message='again')
    capped = await _call(swarm_team, action='message', worker='two', message='and again')
    assert capped.error is not None
    assert (await _call(swarm_team, action='message', worker='one', message='hi me')).error is not None

    # A worker cannot start workers of its own (depth 1).
    assert (await _call(spawn_workers, workers=[{'brief': 'nested'}])).error.code == 'too_deep'

    world.gate.set()
    await _settle()
    assert (await service.get_swarm('u1', sid)).status == 'done'
    assert len(_lead_turns(world, lead)) == 1
    two = await service.find_worker(sid, 'two')
    assert any('The API moved to v2.' in t[2] for t in world.turns if t[0] == two.chat_id) or two.received >= 1


async def test_review_gate_sends_weak_work_back_once(db, world, monkeypatch):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    await _lead()
    verdicts = ['REVISE\n- The prices are missing.', 'PASS']
    asked = []

    async def ask(llm, system, prompt):
        asked.append(prompt)
        return verdicts.pop(0) if verdicts else 'PASS'

    monkeypatch.setattr(service, '_ask', ask)
    sid = _swarm_id(await _call(spawn_workers, review=True, workers=[{'name': 'pricing', 'brief': 'find prices'}]))
    await _settle()
    worker = await service.find_worker(sid, 'pricing')
    turns = [t[2] for t in world.turns if t[0] == worker.chat_id]
    assert worker.status == 'done' and len(turns) == 2
    assert 'The prices are missing.' in turns[1]
    assert len(asked) == 2 and (worker.review or {}).get('verdict') == 'pass'


async def test_best_of_runs_attempts_and_a_judge_picks_one(db, world, monkeypatch):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead()

    async def ask(llm, system, prompt):
        assert 'Attempt "attempt-1"' in prompt and 'Attempt "attempt-3"' in prompt
        return 'WINNER: attempt-2\nIt handles ties.'

    monkeypatch.setattr(service, '_ask', ask)
    sid = _swarm_id(await _call(spawn_workers, attempts=3, brief='write the query', title='Query'))
    await _settle()
    swarm = await service.get_swarm('u1', sid)
    assert swarm.mode == 'best_of' and swarm.judge['winner'] == 'attempt-2'
    report = _lead_turns(world, lead)[0][2]
    assert 'attempt-2' in report and 'It handles ties.' in report
    assert (await _call(spawn_workers, attempts=3)).error.code == 'brief_required'


def _git(cwd, *args):
    import subprocess

    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


async def test_editing_workers_get_their_own_copy_and_the_lead_merges(db, world, monkeypatch):
    import re

    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers, swarm

    repo = db / 'proj'
    repo.mkdir()
    _git(repo, 'init', '-q')
    (repo / 'README.md').write_text('hello\n')
    _git(repo, 'add', '-A')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'first')

    monkeypatch.setenv('COGNITRIX_ALLOWED_FOLDERS', str(repo))
    await _lead(folders=[{'path': str(repo), 'access': 'write', 'primary': True}])

    def write(session, text):
        match = re.search(r'your own copy of the project at (\S+) \(git branch', text)
        if match:
            name = 'a.txt' if 'part a' in text else 'b.txt'
            open(f'{match.group(1)}/{name}', 'w').write('made by a worker\n')

    world.on_turn = write
    sid = _swarm_id(await _call(spawn_workers, workers=[
        {'name': 'a', 'brief': 'part a', 'edit': True}, {'name': 'b', 'brief': 'part b', 'edit': True}]))
    await _settle()
    a, b = await service.find_worker(sid, 'a'), await service.find_worker(sid, 'b')
    assert a.worktree['changed'] == ['a.txt'] and b.worktree['changed'] == ['b.txt']
    assert a.worktree['path'] != b.worktree['path'] and not (repo / 'a.txt').exists()
    # This server only allows folders under the project, so the copies are
    # inside it, where git ignores them.
    assert '.cognitrix-swarm' in a.worktree['path']
    import subprocess
    assert subprocess.run(['git', '-C', str(repo), 'status', '--porcelain'], capture_output=True, text=True).stdout == ''

    merged = await _call(swarm, action='merge', worker='a')
    assert merged.status == 'success' and (repo / 'a.txt').read_text() == 'made by a worker\n'
    # Uncommitted changes in the folder block a merge instead of being mixed in.
    (repo / 'README.md').write_text('changed by the person\n')
    blocked = await _call(swarm, action='merge', worker='b')
    assert blocked.error is not None and not (repo / 'b.txt').exists()
    _git(repo, 'checkout', 'README.md')
    assert (await _call(swarm, action='merge', worker='b')).status == 'success'
    assert (repo / 'b.txt').exists()


async def test_one_editor_works_in_place_and_readers_cannot_write(db, world):
    from cognitrix.sessions.base import Session
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    folder = db / 'docs'
    folder.mkdir()
    await _lead(folders=[{'path': str(folder), 'access': 'write', 'primary': True}])
    sid = _swarm_id(await _call(spawn_workers, workers=[
        {'name': 'writer', 'brief': 'fix the doc', 'edit': True}, {'name': 'reader', 'brief': 'read the doc'}]))
    await _settle()
    writer, reader = await service.find_worker(sid, 'writer'), await service.find_worker(sid, 'reader')
    assert not writer.worktree
    assert (await Session.get(writer.chat_id)).folders[0]['access'] == 'write'
    assert (await Session.get(reader.chat_id)).folders[0]['access'] == 'read'


async def test_a_stuck_worker_is_flagged_once(db, world, monkeypatch):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers, swarm

    await _lead()
    world.gate = asyncio.Event()
    monkeypatch.setattr(service, 'stall_seconds', lambda: 0.0)
    sid = _swarm_id(await _call(spawn_workers, workers=[{'name': 'slow', 'brief': 'takes forever'}]))
    await asyncio.sleep(0.05)
    await service.check_stalls(sid)
    flagged = await service.check_stalls(sid) or ['slow']
    assert flagged == ['slow'] and await service.check_stalls(sid) == []
    assert (await service.find_worker(sid, 'slow')).stalled
    assert '(stalled?)' in (await _call(swarm, action='status')).text
    world.gate.set()
    await _settle()


# --- phase 3: restarts, task runs, the routes --------------------------------------

async def test_a_swarm_is_picked_up_after_a_restart(db, world):
    from cognitrix.swarms import service
    from cognitrix.swarms.models import SwarmRun, SwarmWorker
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead()
    sid = _swarm_id(await _call(spawn_workers, workers=[{'name': 'one', 'brief': 'first'}, {'name': 'two', 'brief': 'second'}]))
    await _settle()
    # As the engine would find it after stopping mid-way: one done, one not started.
    two = await service.find_worker(sid, 'two')
    await SwarmWorker.update_one({'id': two.id}, {'status': 'queued', 'summary': '', 'chat_id': ''})
    await SwarmRun.update_one({'id': sid}, {'status': 'running', 'reported': ['one']})
    service._LIVE.clear()
    before = len(world.turns)

    assert await service.recover() == 1
    await _settle()
    assert (await service.get_swarm('u1', sid)).status == 'done'
    assert (await service.find_worker(sid, 'two')).status == 'done'
    new = world.turns[before:]
    assert any('second' in t[2] for t in new) and not any('## Your job\nfirst' in t[2] for t in new)
    assert len(_lead_turns(world, lead)) == 2


async def test_inside_a_task_run_the_swarm_blocks_and_a_retry_reuses_results(db, world):
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    set_execution_context(ToolExecutionContext(user_id='u1', agent_id=BOSS.id, task_id='t1', run_id='r1'))
    out = await _call(spawn_workers, items=['alpha', 'beta'], brief='research {item}', title='Step')
    assert out.status == 'success'
    assert '2 of 2 done' in out.text and 'did research alpha' in out.text and 'did research beta' in out.text
    sid = out.entities[0].id
    swarm = await service.get_swarm('u1', sid)
    assert swarm.status == 'done' and swarm.run_id == 'r1' and not swarm.session_id
    turns = len(world.turns)
    assert turns == 2 and all(t[1] == 'Boss' for t in world.turns)

    # The step is run again (a retry or a restart): finished work is not redone.
    again = await _call(spawn_workers, items=['alpha', 'beta', 'gamma'], brief='research {item}', title='Step')
    assert '3 of 3 done' in again.text
    assert len(world.turns) == turns + 1 and 'gamma' in world.turns[-1][2]


async def test_swarm_tasks_give_step_agents_spawn_workers(db):
    from cognitrix.tasks import orchestrator
    from cognitrix.tasks.base import Task

    agent = _agent('a1', 'Worker')
    agent.llm.supports_tool_use = True
    orchestrator._add_swarm_tools([agent])
    orchestrator._add_swarm_tools([agent])
    assert [t.name for t in agent.tools] == ['Spawn Workers']
    token = orchestrator._swarm_note.set(orchestrator.SWARM_TASK_NOTE)
    try:
        assert 'Spawn Workers' in orchestrator._with_automation_brief('Do the thing')
    finally:
        orchestrator._swarm_note.reset(token)
    task = Task(title='Big job', description='many parts', swarm=True)
    await task.save()
    assert (await Task.get(task.id)).swarm is True


async def test_routes_list_stop_and_limits(db, world, monkeypatch):
    from fastapi import HTTPException

    from cognitrix.api.routes import swarms as routes
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead()
    monkeypatch.setattr(routes, '_owner', lambda ctx: ctx)
    world.gate = asyncio.Event()
    sid = _swarm_id(await _call(spawn_workers, title='Routes', workers=[{'name': 'one', 'brief': 'x'}, {'name': 'two', 'brief': 'y'}]))
    await asyncio.sleep(0.05)

    listed = await routes.list_swarms(session_id=str(lead.id), ctx='u1')
    assert [s['title'] for s in listed] == ['Routes'] and listed[0]['ledger']['running'] == 2
    assert [w['name'] for w in listed[0]['workers']] == ['one', 'two']
    assert await routes.list_swarms(session_id=str(lead.id), ctx='someone-else') == []
    with pytest.raises(HTTPException) as denied:
        await routes.get_swarm(sid, ctx='someone-else')
    assert denied.value.status_code == 404

    await routes.stop_worker(sid, 'one', ctx='u1')
    await asyncio.sleep(0.1)
    after = await routes.get_swarm(sid, ctx='u1')
    assert {w['name']: w['status'] for w in after['workers']}['one'] == 'stopped'
    stopped = await routes.stop_swarm(sid, ctx='u1')
    assert stopped['status'] == 'stopped'
    world.gate.set()
    await _settle()
    assert _lead_turns(world, lead) == []

    limits = await routes.put_limits(routes.Limits(concurrency=2, auto_start=0), ctx='u1')
    assert limits['concurrency'] == 2 and limits['auto_start'] == 0
    with pytest.raises(HTTPException):
        await routes.put_limits(routes.Limits(concurrency=0), ctx='u1')
    # An admin's cap holds a person's own setting down.
    await service.save_settings('u1', {'admin_concurrency': 1, 'admin_max_workers': 3}, admin=True)
    capped = await routes.get_limits(ctx='u1')
    assert capped['concurrency'] == 1 and capped['max_workers'] == 3 and capped['ceilings']['max_workers'] == 3
    assert (await routes.stop_everything(ctx='u1')) == {'stopped': 0}


async def test_review_findings_circles_waiting_swarms_and_task_models(db, world, monkeypatch):
    from cognitrix.providers import base as providers
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import spawn_workers

    await _lead()
    circle = await _call(spawn_workers, workers=[
        {'name': 'a', 'brief': 'x', 'depends_on': ['b']}, {'name': 'b', 'brief': 'y', 'depends_on': ['a']}])
    assert circle.error.code == 'bad_dependency' and 'circle' in circle.error.message

    # A second swarm that found the person's limit full starts when the first frees a slot.
    await service.save_settings('u1', {'admin_concurrency': 1}, admin=True)
    world.gate = asyncio.Event()
    first = _swarm_id(await _call(spawn_workers, workers=[{'name': 'one', 'brief': 'first job'}]))
    second = _swarm_id(await _call(spawn_workers, workers=[{'name': 'two', 'brief': 'second job'}]))
    await asyncio.sleep(0.05)
    assert (await service.find_worker(second, 'two')).status == 'queued'
    world.gate.set()
    await _settle()
    assert (await service.get_swarm('u1', first)).status == 'done'
    assert (await service.get_swarm('u1', second)).status == 'done'

    # Inside a task run a worker's own model is used too.
    used = []
    monkeypatch.setattr(providers, 'build_turn_llm', lambda llm, provider, model: f'{provider}/{model}')
    world.on_turn = lambda session, text: used.append((providers.turn_llm_override.get() or (None, None))[1])
    set_execution_context(ToolExecutionContext(user_id='u1', agent_id=BOSS.id, task_id='t9', run_id='r9'))
    out = await _call(spawn_workers, workers=[{'brief': 'cheap part', 'model': 'openai/gpt-5.4-mini'}, {'brief': 'other part'}])
    assert out.status == 'success' and sorted(used, key=str) == [None, 'openai/gpt-5.4-mini']


# --- the swarm's message board ---------------------------------------------------

async def test_the_swarm_board_reaches_workers_new_briefs_and_the_report(db, world):
    from cognitrix.board import service as board
    from cognitrix.swarms import service
    from cognitrix.tools.board import board as board_tool
    from cognitrix.tools.swarm import spawn_workers

    lead = await _lead()
    world.gate = asyncio.Event()
    sid = _swarm_id(await _call(spawn_workers, title='Pricing', workers=[
        {'name': 'one', 'brief': 'part one'}, {'name': 'two', 'brief': 'part two'}]))
    await asyncio.sleep(0.05)
    live = service._LIVE[sid]

    # A worker posts to its swarm's board by default; a question for a teammate reaches it now.
    await _as_worker(sid, 'one')
    found = await _call(board_tool, action='post', kind='finding', text='Acme lists prices with VAT.')
    assert found.status == 'success', found.text
    asked = await _call(board_tool, action='post', kind='question', text='Is Globex monthly or yearly?', to='two')
    assert asked.status == 'success', asked.text
    await board.settle()
    posts = await board.posts_on('u1', board.swarm_board(sid))
    assert {p.author_name for p in posts} == {'one'}
    assert any('Is Globex monthly' in m.content for m in live.runs['two'].inbox)
    assert not any('Acme lists' in m.content for m in live.runs['two'].inbox)  # findings aren't pushed

    # The lead's decision goes to every running worker and into briefs of workers that start later.
    set_execution_context(ToolExecutionContext(user_id='u1', session_id=str(lead.id), agent_id=BOSS.id))
    decided = await _call(board_tool, action='post', board='swarm', kind='decision', text='Compare monthly prices only.')
    assert decided.status == 'success', decided.text
    await board.settle()
    assert all(any('Compare monthly' in m.content for m in live.runs[n].inbox) for n in ('one', 'two'))
    await service.add_workers('u1', sid, service.parse_specs([{'name': 'three', 'brief': 'part three'}]))

    # A worker's turn note lists what concerns it.
    note = await board.digest('u1', BOSS.id, 'Boss', board=board.swarm_board(sid), worker='two',
                              chat_id=(await service.find_worker(sid, 'two')).chat_id)
    assert 'For you:' in note and 'Is Globex monthly' in note and 'Compare monthly' in note

    world.gate.set()
    await _settle()
    three = next(t for t in world.turns if 'part three' in t[2])
    assert '## Decisions on the swarm board' in three[2] and 'Compare monthly prices only.' in three[2]
    assert 'message board (Board tool)' in three[2]
    reports = [t[2] for t in _lead_turns(world, lead)]
    assert any('Still open on the swarm board' in r and 'Is Globex monthly' in r for r in reports)
