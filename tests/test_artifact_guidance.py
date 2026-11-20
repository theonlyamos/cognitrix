"""Artifacts as the normal way agents deliver work (artifact_pages/guidance.py).

- Agents with the artifact tools get guidance and the chat's / task's
  existing artifacts in their system message.
- List Artifacts finds earlier artifacts; the artifact tools are always sent
  even when tools load on demand.
- A finished task run's substantial result is kept as an artifact, one per
  task with a version per run, unless the run published one or it's off.
- A finished swarm's results are kept as one artifact in the lead's chat.
- An artifact says where it came from.
"""
from types import SimpleNamespace

import pytest

from tests.test_artifact_pages import _as, _ctx, _tool, _user, db  # noqa: F401

LONG = '# Weekly report\n\n' + '\n'.join(f'- finding {i}: something worth keeping around' for i in range(60))


async def test_prompt_block_has_guidance_and_this_chats_artifacts(db):  # noqa: F811
    from cognitrix.artifact_pages import comments, store
    from cognitrix.artifact_pages.guidance import GUIDANCE, context_block

    amos = await _user('Amos', 'amos@example.com')
    key = str(amos.id)
    page, _ = await store.publish(user_key=key, title='Laptop shortlist', content='<p>x</p>', session_id='s1', agent_id='a1')
    await store.publish(user_key=key, title='Elsewhere', content='<p>y</p>', session_id='s2')
    await comments.add(page, author_id=key, author_name='Amos', body='Add prices')

    block = await context_block(key, session_id='s1')
    assert GUIDANCE in block
    assert f'"Laptop shortlist" (artifact_id {page.id}, html, version 1, 1 open comment)' in block
    assert 'Elsewhere' not in block

    empty = await context_block(key, session_id='s3')
    assert 'already made' not in empty
    task_block = await context_block(key, task_id='t1')
    assert 'In a task run' in task_block


async def test_list_artifacts_finds_by_title_and_scope(db):  # noqa: F811
    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    await _tool('publish_artifact', title='Q3 revenue report', content='<p>1</p>')
    _as(amos, session_id='s2', agent_id='a1')
    await _tool('publish_artifact', title='Trip plan', content='<p>2</p>')

    found = await _tool('list_artifacts', query='revenue')
    assert 'Q3 revenue report' in found.text and 'Trip plan' not in found.text
    here = await _tool('list_artifacts', where='chat')
    assert 'Trip plan' in here.text and 'Q3' not in here.text
    none = await _tool('list_artifacts', query='nothing like this')
    assert none.text == 'No artifacts found.'


def test_artifact_tools_are_core_and_low_risk():
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
    from cognitrix.tools.code_mode import CORE_TOOL_NAMES

    assert {'publish artifact', 'read artifact', 'list artifacts'} <= CORE_TOOL_NAMES
    assert DestructiveOpDetector().analyze('List Artifacts', {}).risk_level == RiskLevel.LOW


def test_worth_keeping():
    from cognitrix.artifact_pages.guidance import worth_keeping

    assert not worth_keeping('Done. Nothing new today.')
    assert worth_keeping(LONG)
    assert worth_keeping('## A\ntext ' * 3 + '\n- one\n- two\n' + 'x' * 400)


def _run(run_id, status='completed'):
    return SimpleNamespace(id=run_id, status=status, requested_by=None, completed_at='2026-11-20T08:00:00Z')


async def test_task_results_become_one_artifact_with_a_version_per_run(db):  # noqa: F811
    from cognitrix.artifact_pages import store
    from cognitrix.artifact_pages.guidance import save_task_result

    amos = await _user('Amos', 'amos@example.com')
    task = SimpleNamespace(id='t1', owner_id=str(amos.id), title='Weekly report', assigned_agents=['a1'],
                           result_artifact=None)

    first = await save_task_result(task, _run('r1'), LONG)
    assert first is not None and first.format == 'markdown' and first.task_id == 't1' and first.agent_id == 'a1'
    again = await save_task_result(task, _run('r1'), LONG)  # the same run twice: no new version
    assert again is None
    second = await save_task_result(task, _run('r2'), LONG + '\n- one more')
    assert second.id == first.id and second.version == 2

    assert await save_task_result(task, _run('r3'), 'Short answer.') is None
    assert await save_task_result(task, _run('r4', 'failed'), LONG) is None
    task.result_artifact = 'off'
    assert await save_task_result(task, _run('r5'), LONG) is None
    task.result_artifact = 'always'
    assert (await save_task_result(task, _run('r6'), 'Short answer.')).version == 3
    origin = await store.origin_info(await store.get_page(str(first.id)))
    assert origin is None  # the task is not a stored Task here


async def test_a_run_that_published_its_own_page_is_left_alone(db):  # noqa: F811
    from cognitrix.artifact_pages import store
    from cognitrix.artifact_pages.guidance import save_task_result

    amos = await _user('Amos', 'amos@example.com')
    await store.publish(user_key=str(amos.id), title='Status board', content='<p>live</p>', task_id='t1', run_id='r1')
    task = SimpleNamespace(id='t1', owner_id=str(amos.id), title='Status', assigned_agents=[], result_artifact=None)
    assert await save_task_result(task, _run('r1'), LONG) is None


async def test_swarm_results_are_kept_in_the_lead_chat(db, tmp_path):  # noqa: F811
    from cognitrix.artifact_pages import store
    from cognitrix.artifact_pages.guidance import save_swarm_report

    amos = await _user('Amos', 'amos@example.com')
    result = tmp_path / 'w1.md'
    result.write_text('Full findings from the first worker.', encoding='utf-8')
    swarm = SimpleNamespace(id='sw1', user_id=str(amos.id), title='Vendor research', agent_id='a1', session_id='s1',
                            task_id='', run_id='', mode='fanout', judge={})
    workers = [
        SimpleNamespace(name='w1', role='pricing', status='done', summary='short', result_path=str(result), error='', position=0),
        SimpleNamespace(name='w2', role='', status='failed', summary='', result_path='', error='Timed out', position=1),
    ]
    page = await save_swarm_report(swarm, workers)
    assert page.title == 'Vendor research (swarm results)' and page.session_id == 's1'
    source = store.read_source(page, 1)
    assert 'Full findings from the first worker.' in source and 'Timed out' in source

    workers[0].status = 'failed'
    assert await save_swarm_report(SimpleNamespace(**{**swarm.__dict__, 'title': 'Other'}), workers) is None


async def test_origin_names_the_chat(db):  # noqa: F811
    from cognitrix.artifact_pages import store
    from cognitrix.sessions.base import Session

    amos = await _user('Amos', 'amos@example.com')
    chat = Session(user_id=str(amos.id), title='Planning the offsite')
    await chat.save()
    page, _ = await store.publish(user_key=str(amos.id), title='Offsite plan', content='# Plan', fmt='markdown',
                                  session_id=str(chat.id))
    origin = await store.origin_info(page)
    assert origin == {'kind': 'chat', 'id': str(chat.id), 'title': 'Planning the offsite'}


async def test_result_artifact_setting_round_trips(db):  # noqa: F811
    from fastapi import HTTPException

    from cognitrix.api.routes.automations import ResultArtifactBody, save_result_artifact
    from cognitrix.tasks.base import Task

    amos = await _user('Amos', 'amos@example.com')
    task = Task(title='Digest', description='d', owner_id=str(amos.id))
    await task.save()
    out = await save_result_artifact(str(task.id), ResultArtifactBody(result_artifact='off'), _ctx(amos))
    assert out == {'result_artifact': 'off'}
    assert (await Task.get(str(task.id))).result_artifact == 'off'
    out = await save_result_artifact(str(task.id), ResultArtifactBody(result_artifact='auto'), _ctx(amos))
    assert out == {'result_artifact': 'auto'} and (await Task.get(str(task.id))).result_artifact is None
    with pytest.raises(HTTPException):
        await save_result_artifact(str(task.id), ResultArtifactBody(result_artifact='sometimes'), _ctx(amos))


async def test_two_callers_on_the_same_run_publish_once(db):  # noqa: F811
    import asyncio

    from cognitrix.artifact_pages.guidance import ensure_schema, save_task_result
    from cognitrix.artifact_pages.models import ArtifactPage, ArtifactVersion

    await ensure_schema()
    amos = await _user('Amos', 'amos@example.com')
    task = SimpleNamespace(id='t1', owner_id=str(amos.id), title='Weekly report', assigned_agents=[], result_artifact=None)
    results = await asyncio.gather(*(save_task_result(task, _run('r1'), LONG) for _ in range(4)))
    assert sum(1 for r in results if r is not None) == 1
    assert len(await ArtifactPage.find({'task_id': 't1'}) or []) == 1
    assert len(await ArtifactVersion.find({'run_id': 'r1'}) or []) == 1
