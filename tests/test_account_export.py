"""Exporting your own data: yours only, and never a key."""
import json

from tests.test_session_ownership_routes import _ctx, _initialize_routes_db, _owned_session


async def test_export_holds_only_the_persons_own_data_and_no_keys(tmp_path):
    from cognitrix.agents import Agent
    from cognitrix.api.routes import account
    from cognitrix.memory import saved
    from cognitrix.models.memory import Memory, MemorySettings
    from cognitrix.providers.base import LLM
    from cognitrix.tasks import Task
    from cognitrix.teams.base import Team

    await _initialize_routes_db(tmp_path)
    for model in (Agent, Task, Team, Memory, MemorySettings):
        await model._create_table_async()

    mine = Agent(name='Mine', system_prompt='Be kind.', owner_id='user-1',
                 llm=LLM(provider='ollama', model='llama3', api_key='sk-very-secret'))
    theirs = Agent(name='Theirs', system_prompt='x', owner_id='user-2', llm=LLM(provider='ollama', model='llama3'))
    await mine.save()
    await theirs.save()
    await _owned_session('user-1', str(mine.id), chat=[
        {'role': 'user', 'type': 'text', 'content': 'Plan the launch'},
        {'role': 'assistant', 'type': 'image', 'content': 'data:image/png;base64,AAAA'},
        {'role': 'assistant', 'type': 'text', 'content': 'Here is a plan.'},
    ])
    await _owned_session('user-1', str(mine.id), incognito=True,
                         chat=[{'role': 'user', 'type': 'text', 'content': 'Off the record'}])
    await _owned_session('user-2', str(theirs.id), chat=[{'role': 'user', 'type': 'text', 'content': 'Not yours'}])
    await saved.add('user-1', 'Prefers short answers.', source='user')
    await saved.add('user-2', 'Someone else entirely.', source='user')

    response = await account.export_my_data(_ctx('user-1'))
    assert 'attachment; filename="cognitrix-export-' in response.headers['content-disposition']
    text = response.body.decode()
    data = json.loads(text)

    assert [a['name'] for a in data['agents']] == ['Mine']
    assert 'sk-very-secret' not in text
    assert len(data['chats']) == 1
    assert [m['content'] for m in data['chats'][0]['messages']] == ['Plan the launch', 'Here is a plan.']
    assert 'Off the record' not in text and 'Not yours' not in text
    assert [m['content'] for m in data['memories']] == ['Prefers short answers.']
    assert data['cognitrix_export'] == 1
