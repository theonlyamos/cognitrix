"""Standing assistants (brief, check-ins, read-only runs, offers), skill
drafts with review, "only when I ask" / "hand off to me" tool levels, and
Stop all."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
from cognitrix.api.routes import agents as agent_routes
from cognitrix.api.routes import assistant
from cognitrix.common.security import AuthContext
from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM
from cognitrix.safety import permissions
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
from cognitrix.safety.permissions import decide_tool_call, normalize_tool_permissions, read_only_block
from cognitrix.skills import drafts
from cognitrix.tasks import standing
from cognitrix.tasks.automation import AgentNotification, TaskTrigger, TriggerEvent
from cognitrix.tasks.base import Task
from cognitrix.tasks.run import TaskRun, TaskRunStatus
from cognitrix.tools.resilient_tool_wrapper import ToolResult
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

LOW = RiskAssessment(RiskLevel.LOW, [], 'fine')
HIGH = RiskAssessment(RiskLevel.HIGH, ['deletion'], 'deletes')
BRIEF = {'keep_true': 'Client mail is answered within a day', 'sources': 'Gmail inbox',
         'tell_me': 'Anything from Acme', 'not_yet': 'Never send mail'}


def _llm():
    return LLM(provider='openai', base_url='http://x', api_key='k', model='m')


def _tool(name, **kw):
    return Tool(name=name, description='d', parameters={}, **kw)


def _ctx(user='u1', role='admin'):
    return AuthContext(user=SimpleNamespace(id=user, role=role, email=f'{user}@x.test'), api_key=None)


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.run import TaskRunHead

    db_file = str(tmp_path / 'teammate.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    for model in (Agent, Task, TaskRun, TaskRunHead, TaskTrigger, TriggerEvent, AgentNotification,
                  standing.AgentOffer, drafts.SkillDraft):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()


async def _agent(owner='u1', **kw):
    agent = Agent(name=kw.pop('name', 'Ada'), llm=_llm(), system_prompt='sys', owner_id=owner, **kw)
    await agent.save()
    return agent


@pytest.fixture
def enqueue(monkeypatch):
    import cognitrix.api.routes.tasks as task_routes

    calls: list = []

    async def fake_enqueue(task, resume=False, **kwargs):
        calls.append((task.id, kwargs))
        return SimpleNamespace(id=f'run-{len(calls)}', status='queued')

    monkeypatch.setattr(task_routes, '_enqueue_task_start', fake_enqueue)
    return calls


# --- tool levels -------------------------------------------------------------------

def test_new_levels_are_accepted_and_stored():
    cleaned = normalize_tool_permissions({'Send Mail': 'told', 'Pay': 'handoff', 'Read': 'auto'})
    assert cleaned == {'Send Mail': 'told', 'Pay': 'handoff'}
    agent = Agent(name='A', llm=_llm(), system_prompt='s', tool_permissions=cleaned)
    assert permissions.stored_tool_permissions(agent) == cleaned


def test_only_when_i_ask_runs_in_a_chat_and_asks_when_unattended():
    agent = Agent(name='A', llm=_llm(), system_prompt='s', tool_permissions={'Delete File': 'told'})
    tool = _tool('Delete File')
    assert decide_tool_call(agent, tool, {}, HIGH, interface='web').action == 'run'
    for interface in ('task', 'auto'):
        decision = decide_tool_call(agent, tool, {}, HIGH, interface=interface)
        assert decision.action == 'ask' and permissions.PERMISSION_ASK_CATEGORY in decision.risk.categories
    token = permissions.unattended_turn.set(True)
    try:
        assert decide_tool_call(agent, tool, {}, HIGH, interface='web').action == 'ask'
    finally:
        permissions.unattended_turn.reset(token)


def test_hand_off_blocks_and_is_marked():
    agent = Agent(name='A', llm=_llm(), system_prompt='s', tool_permissions={'Pay': 'handoff'})
    decision = decide_tool_call(agent, _tool('Pay'), {}, LOW, interface='web')
    assert decision.action == 'block' and decision.handoff and 'Inbox' in decision.reason
    # the model still sees the tool, so it can prepare the action
    assert [t.name for t in permissions.visible_tools(agent, [_tool('Pay')])] == ['Pay']


def test_app_tool_levels_follow_the_same_rules():
    base = permissions.ToolDecision('ask', HIGH)
    assert permissions.apply_target_level(base, 'handoff', 'gmail_send', HIGH).handoff
    assert permissions.apply_target_level(base, 'told', 'gmail_send', HIGH, interface='web').action == 'run'
    assert permissions.apply_target_level(base, 'told', 'gmail_send', HIGH, interface='task').action == 'ask'


def test_read_only_run_blocks_changes_but_not_reads():
    always = Agent(name='A', llm=_llm(), system_prompt='s', permission_level='allow')
    token = permissions.read_only_run.set('check-ins can only look')
    try:
        read = decide_tool_call(always, _tool('Read'), {}, LOW)
        assert read_only_block(read, _tool('Read'), {}).action == 'run'
        # "always allow" doesn't let a check-in change things
        delete = decide_tool_call(always, _tool('Delete File'), {}, HIGH)
        assert delete.action == 'run'
        blocked = read_only_block(delete, _tool('Delete File'), {})
        assert blocked.action == 'block' and 'only look' in blocked.reason
        # notes and alerts never ask, so they stay usable
        notes = _tool('Update Notes', approval_mode='assigned_only')
        assert read_only_block(decide_tool_call(always, notes, {}, LOW), notes, {}).action == 'run'
    finally:
        permissions.read_only_run.reset(token)
    assert read_only_block(delete, _tool('Delete File'), {}).action == 'run'


@pytest.fixture
def runner(monkeypatch):
    ran = []
    monkeypatch.setattr(
        'cognitrix.agents.base.ToolManager.get_by_name',
        staticmethod(lambda name: Tool(name=name, description='d', parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        ran.append(tool.name)
        return ToolResult(success=True, data='ran')

    monkeypatch.setattr('cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool', fake_run_tool)
    return ran


async def test_hand_off_leaves_the_prepared_action_in_the_inbox(db, runner):
    agent = Agent(name='Ada', llm=_llm(), system_prompt='s', tools=[_tool('Pay Invoice')],
                  tool_permissions={'Pay Invoice': 'handoff'})
    token = set_execution_context(ToolExecutionContext(user_id='u1', session_id='s1'))
    try:
        res = await agent.call_tools(
            [{'name': 'Pay Invoice', 'arguments': {'amount': 120, 'to': 'Acme'}, 'tool_call_id': '1'}],
            interface='web')
    finally:
        reset_execution_context(token)
    assert res['result'][0]['success'] is False and 'Inbox' in res['result'][0]['data']
    assert runner == []
    [note] = await AgentNotification.find({'user_id': 'u1'})
    assert note.title == 'Yours to do: Pay Invoice' and '"amount": 120' in note.message
    assert note.link == '/home?chat=s1'


async def test_read_only_run_refuses_a_change_in_the_agent_loop(db, runner):
    agent = Agent(name='Ada', llm=_llm(), system_prompt='s', tools=[_tool('Delete File'), _tool('Read')],
                  permission_level='allow')
    token = permissions.read_only_run.set(standing.READ_ONLY_REASON)
    try:
        res = await agent.call_tools([
            {'name': 'Delete File', 'arguments': {'path': 'x'}, 'tool_call_id': '1'},
            {'name': 'Read', 'arguments': {'file_path': 'x'}, 'tool_call_id': '2'},
        ], interface='task')
    finally:
        permissions.read_only_run.reset(token)
    by_id = {r['tool_call_id']: r for r in res['result']}
    assert by_id['1']['success'] is False and 'Offer Help' in by_id['1']['data']
    assert runner == ['Read']


# --- standing assistant ------------------------------------------------------------

async def test_turning_check_ins_on_creates_a_scheduled_home_task(db):
    agent = await _agent()
    view = await assistant.save_standing(
        agent.id, assistant.StandingBody(enabled=True, brief=BRIEF, every_minutes=60, primary=True), _ctx())
    assert view['enabled'] and view['task_id'] and view['next_check_at'] and view['primary']
    task = await Task.get(view['task_id'])
    assert task.standing_agent_id == str(agent.id) and task.owner_id == 'u1'
    assert task.schedule_enabled and task.schedule_interval == 3600
    assert task.assigned_agents == [str(agent.id)]
    assert 'Client mail is answered' in task.description
    stored = await Agent.find_one({'id': agent.id})
    assert standing.settings_of(stored)['task_id'] == str(task.id)

    # saving again updates the same task; turning off pauses it and keeps notes
    again = await assistant.save_standing(
        agent.id, assistant.StandingBody(every_minutes=120, notes='last seen: 3 mails'), _ctx())
    assert again['task_id'] == view['task_id'] and again['every_minutes'] == 120
    off = await assistant.save_standing(agent.id, assistant.StandingBody(enabled=False), _ctx())
    assert off['enabled'] is False and off['notes'] == 'last seen: 3 mails' and off['next_check_at'] is None
    task = await Task.get(view['task_id'])
    assert not task.schedule_enabled and task.next_run_at is None


async def test_check_ins_need_a_brief_and_sane_timing(db):
    agent = await _agent()
    with pytest.raises(HTTPException) as exc:
        await assistant.save_standing(agent.id, assistant.StandingBody(enabled=True), _ctx())
    assert exc.value.status_code == 400 and 'brief' in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await assistant.save_standing(
            agent.id, assistant.StandingBody(enabled=True, brief=BRIEF, every_minutes=1), _ctx())
    assert exc.value.status_code == 400


async def test_only_the_owner_sets_up_check_ins(db):
    agent = await _agent()
    with pytest.raises(HTTPException) as exc:
        await assistant.save_standing(
            agent.id, assistant.StandingBody(enabled=True, brief=BRIEF), _ctx('intruder', role='member'))
    assert exc.value.status_code == 404


async def test_one_main_assistant_per_person(db):
    first, second = await _agent(name='Ada'), await _agent(name='Bo')
    await assistant.save_standing(first.id, assistant.StandingBody(primary=True), _ctx())
    await assistant.save_standing(second.id, assistant.StandingBody(primary=True), _ctx())
    first, second = await Agent.find_one({'id': first.id}), await Agent.find_one({'id': second.id})
    assert not standing.settings_of(first)['primary'] and standing.settings_of(second)['primary']
    assert [a.name for a in standing.primary_first([first, second])] == ['Bo', 'Ada']


async def test_agent_save_keeps_standing_settings(db):
    agent = await _agent()
    await assistant.save_standing(agent.id, assistant.StandingBody(enabled=True, brief=BRIEF), _ctx())
    stored = await Agent.find_one({'id': agent.id})
    incoming = Agent(name='Ada', llm=_llm(), system_prompt='sys', automation={'self_checkins': False})
    agent_routes._check_policy(incoming, {'automation': {'self_checkins': False}}, stored)
    assert incoming.automation['self_checkins'] is False
    assert incoming.automation['standing']['brief'] == BRIEF
    # a client can't set them through the agent form
    sneaky = Agent(name='Ada', llm=_llm(), system_prompt='sys',
                   automation={'standing': {'enabled': True, 'task_id': 'other'}})
    agent_routes._check_policy(sneaky, {'automation': sneaky.automation}, None)
    assert 'standing' not in sneaky.automation


async def test_check_now_starts_a_run(db, enqueue):
    agent = await _agent()
    with pytest.raises(HTTPException) as exc:
        await assistant.check_now(agent.id, _ctx())
    assert exc.value.status_code == 409
    view = await assistant.save_standing(agent.id, assistant.StandingBody(enabled=True, brief=BRIEF), _ctx())
    out = await assistant.check_now(agent.id, _ctx())
    assert out['run_id'] == 'run-1' and enqueue[0][0] == view['task_id']


async def test_check_in_context_restates_the_brief_and_remembers_offers(db):
    agent = await _agent()
    view = await assistant.save_standing(agent.id, assistant.StandingBody(enabled=True, brief=BRIEF), _ctx())
    task = await Task.get(view['task_id'])
    agent = await standing.standing_agent(task)
    run = TaskRun(task_id=task.id, status=TaskRunStatus.RUNNING, requested_by='u1')
    await run.save()

    made = await standing.create_offer(task, run, agent, 'Draft replies', 'Three mails wait', 'Draft, do not send')
    assert isinstance(made, standing.AgentOffer)
    assert 'already made' in await standing.create_offer(task, run, agent, 'draft replies', 'x', 'y')
    await standing.create_offer(task, run, agent, 'Tidy the calendar', 'w', 'p')
    old = (await standing.offers_for('u1'))[1]
    await standing.decide_offer(old, standing.OFFER_DISMISSED)

    text = await standing.run_context(task, agent)
    assert 'Never send mail' in text and 'cannot send, change' in text
    assert 'do not repeat): Draft replies' in text
    assert 'do not offer these again): Tidy the calendar' in text

    await standing.create_offer(task, run, agent, 'Third', 'w', 'p')
    assert 'at most' in await standing.create_offer(task, run, agent, 'Fourth', 'w', 'p')

    # offers can be turned off
    settings = standing.settings_of(agent)
    settings['offers'] = False
    await standing.save_settings(agent, settings)
    assert 'Offer Help' not in await standing.run_context(task, agent)
    run2 = TaskRun(task_id=task.id, status=TaskRunStatus.RUNNING, requested_by='u1')
    await run2.save()
    assert 'turned offers off' in await standing.create_offer(task, run2, agent, 'Fifth', 'w', 'p')


async def test_offer_tool_only_works_in_a_check_in(db):
    from cognitrix.tools.automation import offer_help

    plain = Task(title='t', description='d', owner_id='u1', assigned_agents=['a'])
    await plain.save()
    run = TaskRun(task_id=plain.id, status=TaskRunStatus.RUNNING, requested_by='u1')
    await run.save()
    token = set_execution_context(ToolExecutionContext(user_id='u1', task_id=str(plain.id), run_id=str(run.id)))
    try:
        outcome = await offer_help.run(title='Help', why='w', plan='p')
    finally:
        reset_execution_context(token)
    assert outcome.outcome.error.code == 'not_a_check_in'


async def test_accepting_an_offer_starts_a_chat_once(db, monkeypatch):
    from cognitrix.sessions import agent_chats

    agent = await _agent()
    row = standing.AgentOffer(user_id='u1', agent_id=str(agent.id), agent_name='Ada',
                              title='Draft replies', details='why', plan='Draft three replies')
    await row.save()
    sent = []

    async def fake_create(user_key, agent_, started_by):
        return SimpleNamespace(id='chat-1', started_by=started_by)

    monkeypatch.setattr(agent_chats, 'create_chat', fake_create)
    monkeypatch.setattr(agent_chats, 'send_turn', lambda *a: sent.append(a))

    [listed] = await assistant.list_offers(_ctx())
    assert listed['title'] == 'Draft replies'
    with pytest.raises(HTTPException):
        await assistant.accept_offer(row.id, _ctx('intruder'))
    out = await assistant.accept_offer(row.id, _ctx())
    assert out['session_id'] == 'chat-1'
    assert 'Draft three replies' in sent[0][3] and sent[0][2].started_by['offer_id'] == str(row.id)
    with pytest.raises(HTTPException) as exc:
        await assistant.accept_offer(row.id, _ctx())
    assert exc.value.status_code == 409
    assert await assistant.list_offers(_ctx()) == []


async def test_dismissing_an_offer(db):
    agent = await _agent()
    row = standing.AgentOffer(user_id='u1', agent_id=str(agent.id), title='T', plan='p')
    await row.save()
    assert (await assistant.dismiss_offer(row.id, _ctx()))['status'] == 'dismissed'
    with pytest.raises(HTTPException):
        await assistant.dismiss_offer(row.id, _ctx())


# --- skill drafts ------------------------------------------------------------------

BODY = '## Steps\n1. Read the sales sheet.\n2. Sum by region.\n\n## Output\nA short table.'


@pytest.fixture
def skills(monkeypatch):
    made = {}

    class FakeManager:
        async def get_skill(self, name):
            return made.get(name)

        async def create_skill(self, name, description, body, **front):
            made[name] = SimpleNamespace(name=name, description=description, body=body, front=front)
            return made[name]

    monkeypatch.setattr('cognitrix.skills.manager.get_skill_manager', lambda: FakeManager())
    return made


def test_draft_names_and_bodies_are_checked():
    assert drafts.clean('Weekly Sales Summary!', ' when  asked ', BODY)[0] == 'weekly-sales-summary'
    for bad in (('', 'd', BODY), ('ok', '', BODY), ('ok', 'd', 'too short')):
        with pytest.raises(drafts.DraftError):
            drafts.clean(*bad)


def test_model_answer_is_parsed():
    import json

    answer = '```json\n' + json.dumps({'name': 'Sales Summary', 'description': 'When asked', 'body': BODY}) + '\n```'
    assert drafts.parse_draft(answer) == ('sales-summary', 'When asked', BODY)
    with pytest.raises(drafts.DraftError):
        drafts.parse_draft('I could not do that')


def test_transcript_skips_system_text_and_trims_tool_output():
    text = drafts.transcript_of([
        {'role': 'system', 'content': 'secret prompt'},
        {'role': 'user', 'content': 'Summarise sales'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'Read'}}]},
        {'role': 'tool', 'content': 'x' * 5000},
        {'role': 'assistant', 'content': 'Done'},
    ])
    assert 'secret prompt' not in text and 'Person: Summarise sales' in text
    assert '[used tools: Read]' in text and len(text) < 1000


async def test_propose_skill_tool_makes_a_draft_for_review(db, skills):
    from cognitrix.tools.skill_tool import propose_skill

    token = set_execution_context(ToolExecutionContext(user_id='u1', session_id='s1'))
    try:
        said = await propose_skill.run(name='Sales Summary', description='When asked for sales', instructions=BODY)
        again = await propose_skill.run(name='Sales Summary', description='When asked for sales', instructions=BODY)
    finally:
        reset_execution_context(token)
    assert 'waiting' in str(said) and 'already waiting' in str(again)
    assert skills == {}  # nothing usable until approved
    [listed] = await assistant.list_drafts(_ctx())
    assert listed['name'] == 'sales-summary' and listed['mine'] and listed['can_approve']


async def test_review_edit_approve_reject(db, skills):
    row = await drafts.create_draft('u2', 'sales-summary', 'When asked', BODY, agent_name='Ada')
    member, admin = _ctx('u2', role='member'), _ctx('u1')
    # the author can edit but, not being an admin, cannot approve
    edited = await assistant.edit_draft(row.id, assistant.DraftEdit(name='Regional Sales'), member)
    assert edited['name'] == 'regional-sales' and edited['can_approve'] is False
    with pytest.raises(HTTPException) as exc:
        await assistant.approve_draft(row.id, member)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await assistant.edit_draft(row.id, assistant.DraftEdit(), _ctx('u3', role='member'))
    assert exc.value.status_code == 404

    out = await assistant.approve_draft(row.id, admin)
    assert out == {'status': 'approved', 'name': 'regional-sales'}
    assert skills['regional-sales'].body == BODY and skills['regional-sales'].front['author'] == 'Ada'
    with pytest.raises(HTTPException) as exc:
        await assistant.approve_draft(row.id, admin)
    assert exc.value.status_code == 409

    other = await drafts.create_draft('u2', 'regional-sales', 'When asked', BODY)
    with pytest.raises(HTTPException) as exc:  # the name is taken now
        await assistant.approve_draft(other.id, admin)
    assert exc.value.status_code == 409
    assert (await assistant.reject_draft(other.id, member))['status'] == 'rejected'
    assert await assistant.list_drafts(admin) == []


# --- stop all ----------------------------------------------------------------------

async def test_stop_all_stops_chats_runs_and_pending_checks(db, monkeypatch):
    import cognitrix.api.routes.tasks as task_routes
    from cognitrix.sessions import runs as chat_runs

    agent = await _agent()
    other = await _agent(name='Bo')
    mine = chat_runs.start_run('u1', str(agent.id), 'chat-a')
    theirs = chat_runs.start_run('u1', str(other.id), 'chat-b')
    stopped = []
    mine.stop = lambda: stopped.append('chat-a') or True
    theirs.stop = lambda: stopped.append('chat-b') or True

    task = Task(title='watch', description='d', owner_id='u1', assigned_agents=[str(agent.id)],
                wake_pending=True, wake_at='2030-01-01 00:00:00', wake_reason='look again')
    await task.save()
    foreign = Task(title='not mine', description='d', owner_id='u9', assigned_agents=[str(agent.id)],
                   wake_pending=True, wake_at='2030-01-01 00:00:00')
    await foreign.save()
    run = SimpleNamespace(id='r1', status=TaskRunStatus.RUNNING)
    cancelled = []

    async def active(task_id):
        return run if task_id == task.id else None

    async def cancel(task_, run_):
        cancelled.append(task_.id)

    async def visible(*a, **k):
        return True

    monkeypatch.setattr(task_routes, '_active_run', active)
    monkeypatch.setattr(task_routes, 'cancel_active_run', cancel)
    monkeypatch.setattr(task_routes, '_run_visible', visible)
    monkeypatch.setattr('cognitrix.tasks.run.run_acl_allowed', lambda *a: True)
    try:
        out = await assistant.stop_all(agent.id, _ctx())
    finally:
        mine.finish({'type': 'turn_stopped'})
        theirs.finish({'type': 'turn_stopped'})
    assert out == {'chats': 1, 'task_runs': 1, 'checks': 1}
    assert stopped == ['chat-a'] and cancelled == [task.id]
    assert not (await Task.get(task.id)).wake_pending
    assert (await Task.get(foreign.id)).wake_pending


def test_read_only_run_refuses_changes_that_never_ask():
    """Edit, a new file and undoable app changes run without asking, yet are changes."""
    from cognitrix.safety.destructive_ops import mcp_only_reads

    always = Agent(name='A', llm=_llm(), system_prompt='s', permission_level='allow')
    token = permissions.read_only_run.set('check-ins can only look')
    try:
        for name in ('Edit', 'Write', 'Create Task', 'Message Chat', 'Some New Tool'):
            tool = _tool(name)
            decision = decide_tool_call(always, tool, {}, LOW)
            assert decision.action == 'run'
            assert read_only_block(decision, tool, {}).action == 'block', name
        for name in ('Read', 'Grep', 'Offer Help', 'Notify Me', 'Search Knowledge'):
            tool = _tool(name)
            assert read_only_block(decide_tool_call(always, tool, {}, LOW), tool, {}).action == 'run', name

        http = _tool('Http Request')
        ran = decide_tool_call(always, http, {}, LOW)
        assert read_only_block(ran, http, {'url': 'https://x.test'}).action == 'run'
        assert read_only_block(ran, http, {'url': 'https://x.test', 'method': 'post'}).action == 'block'

        app = _tool('Run Mcp Tool')
        app.category = 'mcp'
        ran = decide_tool_call(always, app, {}, LOW)
        assert read_only_block(ran, app, {}).action == 'block'  # unknown counts as a change
        assert read_only_block(ran, app, {}, mcp_reads=False).action == 'block'
        assert read_only_block(ran, app, {}, mcp_reads=True).action == 'run'
    finally:
        permissions.read_only_run.reset(token)

    assert mcp_only_reads('anything', {'readOnlyHint': True})
    assert mcp_only_reads('search_threads', None)
    assert not mcp_only_reads('create_draft', None)
    assert not mcp_only_reads('get_and_delete', None)
    assert not mcp_only_reads('search_threads', {'readOnlyHint': False})
    assert not mcp_only_reads('frobnicate', None)


def test_accepted_offer_carries_the_briefs_limits():
    agent = Agent(name='A', llm=_llm(), system_prompt='s')
    agent.automation = {'standing': {'enabled': True, 'brief': {
        'keep_true': 'Mail gets answered', 'not_yet': 'Never send mail. Drafts only.'}}}
    row = standing.AgentOffer(user_id='u', agent_id=str(agent.id), title='Draft replies', plan='Write drafts')
    text = standing.accept_prompt(row, agent)
    assert 'Write drafts' in text and 'Never send mail. Drafts only.' in text
    assert 'Limits' not in standing.accept_prompt(row)
