"""Tasks and teams belong to the user who created them through the web API."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'ownership.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    from cognitrix.tasks.run import TaskRun, TaskRunHead

    for model in (TaskRun, TaskRunHead):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()
    monkeypatch.delenv('COGNITRIX_ADMIN_EMAILS', raising=False)
    return db_file


async def _users():
    """Alice signed up first (admin); Bob signed up later."""
    from cognitrix.common.security import AuthContext
    from cognitrix.models.user import User

    alice = User(name='Alice', email='alice@example.com', password='x')
    await alice.save()
    bob = User(name='Bob', email='bob@example.com', password='x')
    await bob.save()
    alice_row = await User.find_one({'email': 'alice@example.com'})
    bob_row = await User.find_one({'email': 'bob@example.com'})
    # Make the signup order explicit regardless of timestamp resolution.
    await User.update_one({'id': alice_row.id}, {'created_at': '2020-01-01 00:00:00'})
    await User.update_one({'id': bob_row.id}, {'created_at': '2021-01-01 00:00:00'})
    return AuthContext(user=alice_row), AuthContext(user=bob_row)


def _request():
    return SimpleNamespace(headers={}, client=None)


async def _create_task(ctx, **fields):
    from fastapi import BackgroundTasks

    import cognitrix.api.routes.tasks as routes
    from cognitrix.tasks import Task

    task = Task(title=fields.pop('title', 'mine'), description='d', **fields)
    out = await routes.save_task(_request(), task, BackgroundTasks(), ctx)
    return out['id']


@pytest.mark.asyncio
async def test_tasks_are_private_to_their_creator(db):
    import cognitrix.api.routes.tasks as routes
    from cognitrix.tasks import Task

    alice, bob = await _users()
    task_id = await _create_task(alice)
    stored = await Task.get(task_id)
    assert stored.owner_id == str(alice.user.id)

    assert [t['id'] for t in await routes.list_tasks(alice)] == [task_id]
    assert await routes.list_tasks(bob) == []
    assert await routes.load_task(task_id, bob) == {}
    for call in (
        routes.delete_task(task_id, bob),
        routes.start_task_run(task_id, None, bob),
        routes.list_task_runs(task_id, bob),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_owner_cannot_be_changed_by_an_edit(db):
    from fastapi import BackgroundTasks

    import cognitrix.api.routes.tasks as routes
    from cognitrix.tasks import Task

    alice, bob = await _users()
    task_id = await _create_task(alice)

    hijack = Task(id=task_id, title='taken', description='d', owner_id=str(bob.user.id))
    with pytest.raises(HTTPException) as exc:
        await routes.save_task(_request(), hijack, BackgroundTasks(), bob)
    assert exc.value.status_code == 404

    edit = Task(id=task_id, title='renamed', description='d', owner_id=str(bob.user.id))
    await routes.save_task(_request(), edit, BackgroundTasks(), alice)
    stored = await Task.get(task_id)
    assert stored.title == 'renamed'
    assert stored.owner_id == str(alice.user.id)


@pytest.mark.asyncio
async def test_tasks_cannot_use_another_users_agent(db):
    from cognitrix.agents import Agent
    from cognitrix.providers.base import LLM

    alice, bob = await _users()
    agent = Agent(name='private', owner_id=str(alice.user.id), system_prompt='s',
                  llm=LLM(provider='openai', api_key='k-1234567890', model='m'))
    await agent.save()

    with pytest.raises(HTTPException) as exc:
        await _create_task(bob, assigned_agents=[agent.id])
    assert exc.value.status_code == 403
    assert await _create_task(alice, assigned_agents=[agent.id])


@pytest.mark.asyncio
async def test_shared_task_runs_are_visible_to_their_starter(db):
    import cognitrix.api.routes.tasks as routes
    from cognitrix.tasks import Task
    from cognitrix.tasks.run import TaskRun

    alice, bob = await _users()
    shared = Task(title='shared', description='d')
    await shared.save()
    run = TaskRun(task_id=shared.id, requested_by=str(alice.user.id))
    await run.save()

    # Shared tasks stay visible, but Bob can't read Alice's run on one.
    assert [t['id'] for t in await routes.list_tasks(bob)] == [shared.id]
    with pytest.raises(HTTPException) as exc:
        await routes.load_task_run(shared.id, run.id, bob)
    assert exc.value.status_code == 403
    assert await routes.list_task_runs(shared.id, bob) == []
    assert (await routes.load_task_run(shared.id, run.id, alice))['id'] == run.id

    # Only an admin may delete a shared task.
    with pytest.raises(HTTPException) as exc:
        await routes.delete_task(shared.id, bob)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_agent_overview_only_counts_runs_you_may_see(db):
    import cognitrix.api.routes.agents as agent_routes
    from cognitrix.agents import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.tasks import Task
    from cognitrix.tasks.run import TaskRun

    alice, bob = await _users()
    agent = Agent(name='shared helper', system_prompt='s',
                  llm=LLM(provider='openai', api_key='k-1234567890', model='m'))
    await agent.save()
    shared = Task(title='shared', description='d', assigned_agents=[agent.id])
    await shared.save()
    await TaskRun(task_id=shared.id, requested_by=str(alice.user.id)).save()

    seen_by_bob = await agent_routes.agent_overview(agent.id, bob)
    assert seen_by_bob['stats']['task_runs_week'] == 0
    assert seen_by_bob['tasks'][0]['last_run_at'] is None
    seen_by_alice = await agent_routes.agent_overview(agent.id, alice)
    assert seen_by_alice['stats']['task_runs_week'] == 1
    assert seen_by_alice['tasks'][0]['last_run_status']


@pytest.mark.asyncio
async def test_teams_are_private_to_their_creator(db):
    import cognitrix.api.routes.teams as routes
    from cognitrix.teams.base import Team

    alice, bob = await _users()
    saved = await routes.save_team(Team(name='crew', description='d'), alice)
    team_id = saved['id']
    assert (await Team.get(team_id)).owner_id == str(alice.user.id)

    assert [t['id'] for t in await routes.get_all_teams(alice)] == [team_id]
    assert await routes.get_all_teams(bob) == []
    for call in (
        routes.get_team(team_id, bob),
        routes.delete_team(team_id, bob),
        routes.get_tasks_by_team(team_id, bob),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404


def test_worker_refuses_to_run_another_users_agent():
    from cognitrix.tasks.authority import TaskAuthorityError
    from cognitrix.tasks.orchestrator import _check_roster_ownership

    def agent(owner):
        return SimpleNamespace(id=f'a-{owner}', name=f'a-{owner}', owner_id=owner)

    task = SimpleNamespace(owner_id=None)
    run_by_bob = SimpleNamespace(requested_by='bob')

    _check_roster_ownership(task, run_by_bob, [agent(None), agent('bob')])
    with pytest.raises(TaskAuthorityError):
        _check_roster_ownership(task, run_by_bob, [agent('alice')])

    # Runs nobody started directly (scheduler, CLI) act as the task's owner.
    unattended = SimpleNamespace(requested_by=None)
    _check_roster_ownership(SimpleNamespace(owner_id='alice'), unattended, [agent('alice')])
    with pytest.raises(TaskAuthorityError):
        _check_roster_ownership(SimpleNamespace(owner_id=None), unattended, [agent('alice')])
