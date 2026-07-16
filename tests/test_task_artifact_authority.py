from types import SimpleNamespace

import pytest

from cognitrix.tasks.authority import TaskAuthorityError, reconstruct_tool_context


@pytest.mark.asyncio
async def test_task_authority_context_binds_the_run_to_its_task():
    run = SimpleNamespace(
        id='run-1',
        task_id='task-1',
        authority_kind='system',
        authority_id=None,
        acl_agent_ids=['agent-1'],
        acl_team_id=None,
    )
    task = SimpleNamespace(id='task-1')

    context = await reconstruct_tool_context(run, task)

    assert context.run_id == 'run-1'
    assert context.task_id == 'task-1'


@pytest.mark.asyncio
async def test_task_authority_rejects_a_run_loaded_under_another_task():
    run = SimpleNamespace(
        id='run-1',
        task_id='task-1',
        authority_kind='system',
        authority_id=None,
        acl_agent_ids=['agent-1'],
        acl_team_id=None,
    )

    with pytest.raises(TaskAuthorityError, match='task binding'):
        await reconstruct_tool_context(run, SimpleNamespace(id='task-2'))


@pytest.mark.asyncio
async def test_task_authority_rejects_missing_task_identity():
    run = SimpleNamespace(
        id='run-1',
        task_id=None,
        authority_kind='system',
        authority_id=None,
        acl_agent_ids=['agent-1'],
        acl_team_id=None,
    )

    with pytest.raises(TaskAuthorityError, match='task binding'):
        await reconstruct_tool_context(run, SimpleNamespace(id=None))
