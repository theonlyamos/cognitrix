import pytest

from cognitrix.tools.base import ToolManager


def test_management_tools_are_registered_with_array_schemas_and_no_retries():
    team = ToolManager.get_by_name('create_new_team')
    task = ToolManager.get_by_name('create_task')
    assign = ToolManager.get_by_name('assign_task')
    assert team and task and assign
    assert team.retryable is False and task.max_attempts == 1 and assign.approval_mode == 'assigned_only'
    schema = task.to_dict_format()['function']['parameters']
    assert schema['properties']['agent_refs']['type'] == 'array'
    assert schema['properties']['steps']['type'] == 'array'
    assert schema['properties']['start_now']['type'] == 'boolean'
    assert schema['properties']['schedule_enabled']['type'] == 'boolean'
    assert schema['properties']['schedule_interval_seconds']['type'] == 'integer'


@pytest.mark.asyncio
async def test_create_new_team_persists_deduped_members_and_leader(monkeypatch):
    from cognitrix.tools.management import create_new_team

    class Agent:
        def __init__(self, id, name):
            self.id, self.name = id, name
    alice, bob = Agent('a', 'Alice'), Agent('b', 'Bob')
    saved = []

    async def get(ref): return next((a for a in [alice, bob] if a.id == ref), None)
    async def all_(): return [alice, bob]
    monkeypatch.setattr('cognitrix.agents.Agent.get', get)
    monkeypatch.setattr('cognitrix.agents.Agent.all', all_)

    class Team:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 'team1'

        async def save(self):
            saved.append(self)
    monkeypatch.setattr('cognitrix.teams.base.Team', Team)

    out = await create_new_team.run('Research', 'Research work', ['Alice', 'a'], 'Alice')
    assert out.outcome.status == 'success'
    assert saved[0].assigned_agents == ['a'] and saved[0].leader_id == 'a'


@pytest.mark.asyncio
async def test_create_task_rejects_start_without_owner():
    from cognitrix.tools.management import create_task

    out = await create_task.run('T', 'D', start_now=True)
    assert out.outcome.error.code == 'task_owner_required'


@pytest.mark.asyncio
async def test_create_task_persists_authored_steps_in_orchestrator_shape(monkeypatch):
    from cognitrix.tools.management import create_task

    saved = []
    class Task:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 'task1'
        async def save(self): saved.append(self)

    monkeypatch.setattr('cognitrix.tasks.base.Task', Task)
    monkeypatch.setattr('cognitrix.tasks.scheduler.validate_schedule', lambda task, respecified: None)
    out = await create_task.run('T', 'D', steps=['Gather facts', 'Write report'])
    assert out.outcome.status == 'success'
    assert saved[0].step_instructions['0']['step'] == 'Gather facts'
    assert saved[0].step_instructions['1']['step'] == 'Write report'


@pytest.mark.asyncio
async def test_create_task_rejects_enabled_schedule_without_schedule_type(monkeypatch):
    from cognitrix.tools.management import create_task
    out = await create_task.run('T', 'D', schedule_enabled=True)
    assert out.outcome.error.code == 'invalid_schedule'


@pytest.mark.asyncio
async def test_assign_task_uses_partial_update(monkeypatch):
    from cognitrix.tools.management import assign_task

    class Existing:
        id = 'task1'; title = 'T'; team_id = None; assigned_agents = []; status = 'pending'
        async def save(self): raise AssertionError('full-row save must not be used')
    task = Existing()
    monkeypatch.setattr('cognitrix.tools.management._task', lambda ref: _async(task))

    class Agent:
        id = 'agent1'; name = 'A'
    monkeypatch.setattr('cognitrix.tools.management._agents', lambda refs: _async([Agent()]))
    updates = []
    async def update_one(query, values): updates.append((query, values))
    monkeypatch.setattr('cognitrix.tasks.base.Task.update_one', update_one)

    out = await assign_task.run('task1', agent_refs=['agent1'])
    assert out.outcome.status == 'success'
    assert updates == [({'id': 'task1'}, {'assigned_agents': ['agent1'], 'team_id': None})]


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_chat_only_api_key_cannot_write_through_management_tool():
    from cognitrix.models import Agent
    from cognitrix.models.tool import Tool
    from cognitrix.providers.base import LLM
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    llm = LLM(provider='openai', base_url='http://x', api_key='k', model='m')
    agent = Agent(name='A', llm=llm, system_prompt='sys', tools=[
        Tool(name='Create Task', description='d', parameters={}),
    ])
    token = set_execution_context(ToolExecutionContext(
        user_id='u1', api_key_id='key1', scopes=frozenset({'chat'}),
    ))
    try:
        result = await agent.call_tools([{
            'name': 'Create_Task', 'arguments': {'title': 'T', 'description': 'D'},
            'tool_call_id': 'call-1',
        }])
    finally:
        reset_execution_context(token)
    assert 'missing required scope: write' in result['result'][0]['data']


# ── update_task / delete_task ────────────────────────────────────────────────

from contextlib import contextmanager  # noqa: E402


@contextmanager
def _as_user(user_id='u1', **kw):
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context
    token = set_execution_context(ToolExecutionContext(user_id=user_id, **kw))
    try:
        yield
    finally:
        reset_execution_context(token)


def _real_task(**kw):
    from cognitrix.tasks.base import Task
    data = {'title': 'Daily digest', 'description': 'Summarise the news', 'owner_id': 'u1',
            'assigned_agents': ['agent1']}
    data.update(kw)
    data.setdefault('id', 'task-1')
    return Task(**data)


@pytest.fixture
def task_store(monkeypatch):
    """Tasks the tools can find, and the edits/deletes they make."""
    store = {'tasks': [], 'saved': [], 'deleted': []}

    async def get(ref):
        return next((t for t in store['tasks'] if str(t.id) == ref), None)

    async def all_():
        return list(store['tasks'])

    async def live_update(task):
        store['saved'].append(task.model_copy(deep=True))
        return 1

    async def soft_delete(task):
        store['deleted'].append(task.id)

    monkeypatch.setattr('cognitrix.tasks.base.Task.get', get)
    monkeypatch.setattr('cognitrix.tasks.base.Task.all', all_)
    monkeypatch.setattr('cognitrix.api.routes.tasks._update_existing_task_if_live', live_update)
    monkeypatch.setattr('cognitrix.api.routes.tasks.soft_delete_task', soft_delete)
    return store


def test_update_and_delete_tools_are_registered():
    update = ToolManager.get_by_name('update_task')
    delete = ToolManager.get_by_name('delete_task')
    assert update and delete
    assert delete.approval_mode == 'always'
    assert update.retryable is False and delete.max_attempts == 1
    schema = update.to_dict_format()['function']['parameters']
    assert schema['properties']['steps']['type'] == 'array'
    assert schema['properties']['clear_schedule']['type'] == 'boolean'


@pytest.mark.asyncio
async def test_update_task_changes_only_given_fields(task_store):
    from cognitrix.tools.management import update_task
    task = _real_task()
    task_store['tasks'].append(task)
    with _as_user():
        out = await update_task.run('daily DIGEST', title='Morning digest', steps=['Read feeds'])
    assert out.outcome.status == 'success'
    saved = task_store['saved'][0]
    assert saved.title == 'Morning digest'
    assert saved.description == 'Summarise the news'
    assert saved.step_instructions['0']['step'] == 'Read feeds'


@pytest.mark.asyncio
async def test_update_task_sets_schedule_and_authority(task_store):
    from cognitrix.tools.management import update_task
    task = _real_task(schedule_cron='0 9 * * *', schedule_enabled=True)
    task_store['tasks'].append(task)
    with _as_user():
        out = await update_task.run(task.id, schedule_interval_seconds=3600)
    assert out.outcome.status == 'success', out.outcome
    saved = task_store['saved'][0]
    assert saved.schedule_interval == 3600 and saved.schedule_cron is None
    assert saved.schedule_enabled and saved.next_run_at
    assert saved.schedule_requested_by == 'u1' and saved.schedule_authority_kind == 'jwt'


@pytest.mark.asyncio
async def test_update_task_pause_and_clear(task_store):
    from cognitrix.tools.management import update_task
    task = _real_task(schedule_interval=600, schedule_enabled=True, next_run_at='2030-01-01 00:00:00')
    task_store['tasks'].append(task)
    with _as_user():
        paused = await update_task.run(task.id, schedule_enabled=False)
        cleared = await update_task.run(task.id, clear_schedule=True)
    assert paused.outcome.status == 'success' and cleared.outcome.status == 'success'
    assert task_store['saved'][0].schedule_enabled is False and task_store['saved'][0].next_run_at is None
    assert task_store['saved'][0].schedule_interval == 600
    assert task_store['saved'][1].schedule_interval is None


@pytest.mark.asyncio
async def test_update_task_rejects_bad_input(task_store):
    from cognitrix.tools.management import update_task
    task = _real_task()
    task_store['tasks'].append(task)
    with _as_user():
        nothing = await update_task.run(task.id)
        enable = await update_task.run(task.id, schedule_enabled=True)
        short = await update_task.run(task.id, schedule_interval_seconds=5)
    assert nothing.outcome.error.code == 'nothing_to_update'
    assert enable.outcome.error.code == 'invalid_schedule'
    assert short.outcome.error.code == 'invalid_schedule'
    assert task_store['saved'] == []


@pytest.mark.asyncio
async def test_task_tools_cannot_touch_another_users_task(task_store):
    from cognitrix.tools.management import delete_task, update_task
    task = _real_task(owner_id='someone-else')
    task_store['tasks'].append(task)
    with _as_user('u1'):
        upd = await update_task.run(task.id, title='Mine now')
        rm = await delete_task.run('Daily digest')
    assert upd.outcome.error.code == 'invalid_task_reference'
    assert rm.outcome.error.code == 'invalid_task_reference'
    assert task_store['saved'] == [] and task_store['deleted'] == []


@pytest.mark.asyncio
async def test_shared_task_needs_admin(task_store, monkeypatch):
    from cognitrix.tools.management import delete_task
    task = _real_task(owner_id=None)
    task_store['tasks'].append(task)

    async def no_user(_id): return None
    async def not_admin(_user): return False
    monkeypatch.setattr('cognitrix.models.user.User.get', no_user)
    monkeypatch.setattr('cognitrix.agents.access.is_admin', not_admin)
    with _as_user('u1'):
        out = await delete_task.run(task.id)
    assert out.outcome.error.code == 'authorization_denied'
    assert task_store['deleted'] == []


@pytest.mark.asyncio
async def test_scheduled_runs_cannot_manage_tasks(task_store):
    from cognitrix.tools.management import delete_task, update_task
    task = _real_task()
    task_store['tasks'].append(task)
    # Scheduled/webhook runs: no user and no scopes.
    from cognitrix.tools.management import ToolAuthorizationError
    with _as_user(None, task_id=task.id, run_id='r1', scopes=frozenset()):
        with pytest.raises(ToolAuthorizationError, match='missing required scope: write'):
            await update_task.run(task.id, title='x')
        with pytest.raises(ToolAuthorizationError, match='missing required scope: write'):
            await delete_task.run(task.id)
    # Any other context with no person behind it is refused too.
    with _as_user(None, task_id=task.id, run_id='r1'):
        rm = await delete_task.run(task.id)
    assert rm.outcome.error.code == 'authorization_denied'
    assert task_store['deleted'] == []


@pytest.mark.asyncio
async def test_api_key_allowlist_applies_to_existing_task(task_store):
    from cognitrix.tools.management import delete_task
    task = _real_task(assigned_agents=['agent1'])
    task_store['tasks'].append(task)
    with _as_user('u1', api_key_id='k1', scopes=frozenset({'write'}), allowed_agents=frozenset({'other'})):
        out = await delete_task.run(task.id)
    assert out.outcome.error.code == 'authorization_denied'


@pytest.mark.asyncio
async def test_delete_task_deletes_and_reports_active_run(task_store, monkeypatch):
    from cognitrix.tasks.repository import ActiveRunExists
    from cognitrix.tools.management import delete_task
    task = _real_task()
    task_store['tasks'].append(task)
    with _as_user():
        out = await delete_task.run('Daily digest')
    assert out.outcome.status == 'success' and task_store['deleted'] == [task.id]

    async def busy(_task): raise ActiveRunExists()
    monkeypatch.setattr('cognitrix.api.routes.tasks.soft_delete_task', busy)
    with _as_user():
        out = await delete_task.run(task.id)
    assert out.outcome.error.code == 'task_running'


@pytest.mark.asyncio
async def test_deleted_tasks_are_not_found(task_store):
    from cognitrix.tools.management import update_task
    task = _real_task(deleted_at='2026-01-01 00:00:00')
    task_store['tasks'].append(task)
    with _as_user():
        out = await update_task.run(task.id, title='x')
    assert out.outcome.error.code == 'invalid_task_reference'


@pytest.mark.asyncio
async def test_create_task_records_owner(monkeypatch):
    from cognitrix.tools.management import create_task
    saved = []

    class Task:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.id = 'task1'
        async def save(self): saved.append(self)

    monkeypatch.setattr('cognitrix.tasks.base.Task', Task)
    monkeypatch.setattr('cognitrix.tasks.scheduler.validate_schedule', lambda task, respecified: None)
    with _as_user('u1'):
        await create_task.run('T', 'D')
    assert saved[0].owner_id == 'u1'


@pytest.mark.asyncio
async def test_content_edit_of_enabled_schedule_takes_editor_authority(task_store):
    from cognitrix.tools.management import update_task
    task = _real_task(schedule_interval=600, schedule_enabled=True, next_run_at='2030-01-01 00:00:00',
                      schedule_requested_by='u1', schedule_authority_kind='jwt', schedule_authority_id='u1')
    task_store['tasks'].append(task)
    with _as_user('u1', api_key_id='k1', scopes=frozenset({'write', 'run'})):
        out = await update_task.run(task.id, steps=['Do something else'])
    assert out.outcome.status == 'success'
    saved = task_store['saved'][0]
    assert saved.schedule_authority_kind == 'api_key' and saved.schedule_authority_id == 'k1'
    assert saved.next_run_at == '2030-01-01 00:00:00'
