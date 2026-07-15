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
