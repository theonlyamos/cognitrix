"""Spending caps per person, the audit log and removing people."""
import pytest
from fastapi import HTTPException

from cognitrix import audit, usage
from cognitrix.usage import caps, pricing
from tests.test_admin_and_reset import _ctx, people  # noqa: F401  (fixture)

PRICES = pricing.parse({'data': [
    {'id': 'openai/gpt-5.5', 'pricing': {'prompt': '0.000005', 'completion': '0.00003'}},
]})


@pytest.fixture
async def setup(people, monkeypatch):  # noqa: F811
    from cognitrix.models.memory import Memory

    for model in (caps.SpendingCap, audit.AuditEvent, Memory):
        await model._create_table_async()

    async def prices():
        return PRICES
    monkeypatch.setattr(pricing, 'load', prices)
    caps.forget()
    sent = []

    async def fake_notify(user_id, title, message, **kwargs):
        sent.append((user_id, title))
    import cognitrix.tasks.automation as automation
    monkeypatch.setattr(automation, 'create_notification', fake_notify)
    people.sent = sent
    return people


def _spend(user_id, prompt, completion, agent_id='agent-1'):
    token = usage.set_owner(user_id, agent_id)
    try:
        usage.note_model('openai', 'gpt-5.5')
        usage.log({'prompt_tokens': prompt, 'completion_tokens': completion})
    finally:
        usage.reset_owner(token)


async def test_default_cap_stops_a_member_but_not_the_admin(setup):
    ada, bob = str(setup.ada.id), str(setup.bob.id)
    await caps.save(caps.DEFAULT_KEY, mode='custom', monthly_cost=1.0)
    # $0.5 + $0.3 = $0.80: 80%, one warning, still allowed.
    _spend(bob, 100_000, 10_000)
    _spend(ada, 1_000_000, 0)
    await usage.flush()
    assert await caps.blocked_message(bob) is None
    assert setup.sent == [(bob, 'Spending limit almost reached')]
    caps.forget()
    assert await caps.blocked_message(bob) is None
    assert len(setup.sent) == 1  # once a month

    _spend(bob, 40_000, 0)  # $0.20 more: at the cap
    await usage.flush()
    caps.forget()
    message = await caps.blocked_message(bob)
    assert message and 'spending limit' in message and '$1.00' in message
    # The admin spent $5 and the default is for members only.
    assert await caps.blocked_message(ada) is None

    # An admin can give one person no cap, or their own.
    await caps.save(bob, mode='none')
    assert await caps.blocked_message(bob) is None
    await caps.save(bob, mode='custom', monthly_tokens=1_000_000)
    assert await caps.blocked_message(bob) is None
    await caps.save(bob, mode='custom', monthly_tokens=100_000)
    assert await caps.blocked_message(bob)


async def test_a_capped_person_gets_a_message_instead_of_a_model_call(setup, monkeypatch):
    from cognitrix.providers.base import LLM, LLMManager

    bob = str(setup.bob.id)
    await caps.save(bob, mode='custom', monthly_tokens=10)
    _spend(bob, 100, 0)
    await usage.flush()
    caps.forget()

    called = []

    async def never(*args, **kwargs):
        called.append(1)
    monkeypatch.setattr(LLMManager, '_handle_non_streaming_response', never)
    llm = LLM(provider='openai', model='gpt-5.5', api_key='sk-test-0000000000', base_url='https://api.openai.com/v1')
    token = usage.set_owner(bob, 'agent-1')
    try:
        response = await LLMManager.generate_response(llm, [{'role': 'user', 'content': 'hi'}])
    finally:
        usage.reset_owner(token)
    assert response.error and 'spending limit' in response.error
    assert not called

    # A streaming caller (the web chat) gets a stream with the same message.
    token = usage.set_owner(bob, 'agent-1')
    try:
        stream = await LLMManager.generate_response(llm, [{'role': 'user', 'content': 'hi'}], stream=True)
    finally:
        usage.reset_owner(token)
    chunks = [chunk async for chunk in stream]
    assert len(chunks) == 1 and 'spending limit' in chunks[0].error


async def test_swarms_do_not_start_over_the_cap(setup):
    from cognitrix.swarms import service

    bob = str(setup.bob.id)
    await caps.save(bob, mode='custom', monthly_tokens=10)
    _spend(bob, 100, 0)
    await usage.flush()
    caps.forget()
    with pytest.raises(service.SwarmError) as exc_info:
        await service.start_swarm(bob, object(), [])
    assert exc_info.value.code == 'spending_cap'


async def test_admin_cap_routes_and_people_list(setup):
    from cognitrix.api.routes import admin

    ctx = await admin.admin_only(_ctx(setup.ada))
    await admin.set_default_cap(admin.CapBody(monthly_cost=25), ctx)
    assert (await admin.get_default_cap(ctx))['monthly_cost'] == 25
    row = await admin.set_user_cap(str(setup.bob.id), admin.CapBody(mode='custom', monthly_cost=5), ctx)
    assert row['spending']['cap'] == {'source': 'own', 'mode': 'custom', 'monthly_cost': 5.0, 'monthly_tokens': 0}
    listed = {u['email']: u for u in (await admin.list_users(ctx))['users']}
    assert listed['ada@example.com']['spending']['cap']['source'] == 'none'
    await audit.flush()
    actions = [e['action'] for e in (await audit.page())['events']]
    assert 'Changed the spending limit for' in actions and 'Set the default spending limit' in actions


async def test_audit_log_people_see_their_own_and_admins_see_all(setup):
    from types import SimpleNamespace

    from cognitrix.api.routes import audit as routes

    ada, bob = str(setup.ada.id), str(setup.bob.id)
    agent = SimpleNamespace(id='agent-9', name='Researcher')
    token = usage.set_owner(bob, 'agent-9')
    try:
        audit.record_tool(agent, 'Bash', {'command': 'ls -la', 'api_key': 'sk-secret-value'}, 'ok', risk='low')
        audit.record_tool(agent, 'Delete File', {'path': '/tmp/x'}, 'denied', note='refused by a person')
    finally:
        usage.reset_owner(token)
    audit.record('sign_in', 'Signed in', user_id=ada, outcome='ok')
    await audit.flush()

    mine = await routes.list_events('', '', '', '', '', '', 100, _ctx(setup.bob))
    assert not mine['admin'] and 'people' not in mine
    assert {e['action'] for e in mine['events']} == {'Bash', 'Delete File'}
    bash = next(e for e in mine['events'] if e['action'] == 'Bash')
    assert bash['agent_name'] == 'Researcher' and bash['actor'] == 'bob@example.com'
    assert 'ls -la' in bash['detail'] and 'sk-secret-value' not in bash['detail']

    everyone = await routes.list_events('', '', '', '', '', '', 100, _ctx(setup.ada))
    assert everyone['admin'] and len(everyone['events']) == 3
    only_ada = await routes.list_events(ada, '', '', '', '', '', 100, _ctx(setup.ada))
    assert [e['action'] for e in only_ada['events']] == ['Signed in']
    denied = await routes.list_events('', '', 'tool', 'denied', '', '', 100, _ctx(setup.ada))
    assert [e['action'] for e in denied['events']] == ['Delete File']
    found = await routes.list_events('', '', '', '', 'researcher', '', 100, _ctx(setup.ada))
    assert len(found['events']) == 2
    # A member can't widen their view by naming someone else.
    sneaky = await routes.list_events(ada, '', '', '', '', '', 100, _ctx(setup.bob))
    assert {e['action'] for e in sneaky['events']} == {'Bash', 'Delete File'}


async def test_audit_can_be_switched_off(setup, monkeypatch):
    monkeypatch.setenv('COGNITRIX_AUDIT_DAYS', '0')
    audit.record('sign_in', 'Signed in', user_id='x')
    await audit.flush()
    monkeypatch.delenv('COGNITRIX_AUDIT_DAYS')
    assert (await audit.page())['events'] == []


async def test_removing_someone_moves_their_work_and_deletes_their_private_data(setup):
    from cognitrix.agents import Agent as AgentModel
    from cognitrix.api.routes import admin
    from cognitrix.models import User
    from cognitrix.models.memory import Memory
    from cognitrix.sessions.base import Session

    from cognitrix.providers.base import LLM

    ada, bob = str(setup.ada.id), str(setup.bob.id)
    carol = await setup.make('Carol', 'carol@example.com')
    llm = LLM(provider='openai', model='gpt-5.5', api_key='sk-test-0000000000')

    def Agent(**fields):  # noqa: N802
        return AgentModel(llm=llm, system_prompt='x', **fields)
    await Agent(name='Bob helper', owner_id=bob, shared_with={str(carol.id): 'use'}).save()
    await Agent(name='Carol helper', owner_id=str(carol.id), shared_with={bob: 'edit'}).save()
    await Session(agent_id='a', user_id=bob).save()
    await Memory(user_id=bob, content='Bob likes tea').save()
    await caps.save(bob, mode='custom', monthly_cost=3)
    ctx = await admin.admin_only(_ctx(setup.ada))

    assert (await admin.removal_preview(bob, ctx))['agents'] == 1
    with pytest.raises(HTTPException):
        await admin.remove_user(ada, admin.RemoveBody(confirm_email='ada@example.com'), ctx)
    with pytest.raises(HTTPException):
        await admin.remove_user(bob, admin.RemoveBody(confirm_email='wrong@example.com'), ctx)

    result = await admin.remove_user(bob, admin.RemoveBody(confirm_email='BOB@example.com'), ctx)
    assert result['moved'] == {'Agent': 1}
    assert await User.get(bob) is None
    moved = await AgentModel.find_one({'name': 'Bob helper'})
    assert moved.owner_id == ada
    assert bob not in (await AgentModel.find_one({'name': 'Carol helper'})).shared_with
    assert await Session.find({'user_id': bob}) == []
    assert await Memory.find({'user_id': bob}) == []
    assert await caps.get_row(bob) is None

    # Or their work goes with them.
    await Agent(name='Carol only', owner_id=str(carol.id)).save()
    await admin.remove_user(str(carol.id), admin.RemoveBody(keep_work=False, confirm_email='carol@example.com'), ctx)
    assert await AgentModel.find_one({'name': 'Carol only'}) is None
    await audit.flush()
    removed = [e for e in (await audit.page(kind='people'))['events'] if e['action'] == 'Removed']
    assert {e['target'] for e in removed} == {'bob@example.com', 'carol@example.com'}


async def test_the_last_admin_cannot_be_removed(setup):
    from cognitrix.api.routes import admin

    await admin.update_user(str(setup.bob.id), admin.UserUpdate(role='admin'),
                            await admin.admin_only(_ctx(setup.ada)))
    from cognitrix.models import User
    await admin.update_user(str(setup.ada.id), admin.UserUpdate(role='member'),
                            await admin.admin_only(_ctx(await User.get(setup.bob.id))))
    bob_ctx = await admin.admin_only(_ctx(await User.get(setup.bob.id)))
    # Bob is now the only admin; Ada (a member) can be removed, Bob can't remove himself.
    with pytest.raises(HTTPException):
        await admin.remove_user(str(setup.bob.id), admin.RemoveBody(confirm_email='bob@example.com'), bob_ctx)


async def test_moved_automations_are_paused_and_running_tasks_cancelled(setup, monkeypatch):
    from cognitrix import people_removal
    from cognitrix.api.routes import admin
    from cognitrix.api.routes import tasks as task_routes
    from cognitrix.tasks.automation import TaskTrigger
    from cognitrix.tasks.base import Task

    await Task._create_table_async()
    await TaskTrigger._create_table_async()
    bob = str(setup.bob.id)
    await Task(title='Nightly report', description='x', owner_id=bob, schedule_enabled=True,
               schedule_requested_by=bob, schedule_authority_kind='jwt', schedule_authority_id=bob,
               wake_pending=True, wake_requested_by=bob).save()
    await TaskTrigger(task_id='t', owner_id=bob, token_hash='h', requested_by=bob, authority_id=bob).save()

    cancelled = []

    async def active_run(task_id):
        return 'run-1'

    async def cancel(task, run):
        cancelled.append((task.title, run))
    monkeypatch.setattr(task_routes, '_active_run', active_run)
    monkeypatch.setattr(task_routes, 'cancel_active_run', cancel)

    ctx = await admin.admin_only(_ctx(setup.ada))
    result = await admin.remove_user(bob, admin.RemoveBody(confirm_email='bob@example.com'), ctx)
    assert cancelled == [('Nightly report', 'run-1')]
    assert result['paused'] == 2 and 'paused' in result['message']
    task = await Task.find_one({'title': 'Nightly report'})
    assert task.owner_id == str(setup.ada.id)
    assert not task.schedule_enabled and task.schedule_requested_by is None and not task.wake_pending
    trigger = await TaskTrigger.find_one({'token_hash': 'h'})
    assert trigger.owner_id == str(setup.ada.id) and not trigger.enabled and trigger.authority_id is None
    assert people_removal  # module imported for the patch targets
