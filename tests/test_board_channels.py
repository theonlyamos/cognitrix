# ruff: noqa: F811 (the fixtures from test_board are arguments of the same names)
"""The message board, second round: project channels and task threads, claim
and wait, people, outside agents, grants, scheduled posts, /commands and the
Slack mirror."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_board import RESEARCHER, WRITER, _board, _chat, agents, db, started  # noqa: F401 (fixtures)

from cognitrix.tools.utils import ToolExecutionContext, set_execution_context


async def _user(name, email):
    from cognitrix.models.user import User

    row = User(name=name, email=email, password='x')
    await row.save()
    return str(row.id)


async def _project(owner, agents_=('a-res',), name='Launch'):
    from cognitrix.projects.models import Project

    row = Project(user_id=owner, name=name, agents=list(agents_))
    await row.save()
    return str(row.id)


async def _project_chat(agent, owner, project_id, task_id=None):
    from cognitrix.sessions.base import Session

    session = Session(agent_id=agent.id, user_id=owner, project_id=project_id)
    await session.save()
    set_execution_context(ToolExecutionContext(user_id=owner, session_id=str(session.id), agent_id=agent.id,
                                               task_id=task_id))
    return session


@pytest.fixture
def inbox(monkeypatch):
    import cognitrix.tasks.automation as automation

    sent = []

    async def create_notification(user_id, title, message, **kw):
        sent.append(SimpleNamespace(user_id=user_id, title=title, message=message, link=kw.get('link')))

    monkeypatch.setattr(automation, 'create_notification', create_notification)
    return sent


async def test_project_channel_grants_and_task_threads(db, agents, started):
    from cognitrix.board import channels, service
    from cognitrix.tasks.base import Task

    owner = await _user('Ama', 'ama@example.com')
    pid = await _project(owner)
    task = Task(title='Draft the launch post', description='', owner_id=owner, project_id=pid, assigned_agents=['a-res'])
    await task.save()

    await _project_chat(RESEARCHER, owner, pid, task_id=str(task.id))
    posted = await _board(RESEARCHER, action='post', kind='request', text='Someone check the pricing table.')
    assert posted.status == 'success', posted.text
    [post] = await service.posts_on(owner, f'project:{pid}')
    assert post.thread == f'task:{task.id}' and post.thread_title == 'Draft the launch post'

    # Writer is not one of the project's agents: it may read but not post.
    await _project_chat(WRITER, owner, pid)
    refused = await _board(WRITER, action='post', kind='finding', text='Prices are fine.')
    assert refused.error.code == 'not_granted'
    assert 'pricing table' in (await _board(WRITER, action='read')).text
    await channels.save_channel(owner, f'project:{pid}', agents=['a-wri'])
    assert (await _board(WRITER, action='post', kind='finding', text='Prices are fine.')).status == 'success'

    # The turn note lists the channel, with the task's thread.
    note = await service.digest(owner, 'a-wri', 'Writer', board=f'project:{pid}', thread=f'task:{task.id}')
    assert 'Launch' in note and 'Claim an open request' in note

    boards = await service.boards(owner)
    assert any(b['key'] == f'project:{pid}' and b['total'] == 2 for b in boards)


async def test_claim_and_wait(db, agents, started, monkeypatch):
    from cognitrix.board import service

    monkeypatch.setattr(service, 'WAIT_STEP', 0.05)
    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='request', text='Write the release notes.')
    [post] = await service.posts_on('u1', 'agents')

    await _chat(WRITER)
    claimed = await _board(WRITER, action='claim', post_id=str(post.id))
    assert claimed.status == 'success', claimed.text
    await _chat(RESEARCHER)
    taken = await _board(RESEARCHER, action='claim', post_id=str(post.id))
    assert taken.error.code == 'claimed' and 'Writer' in taken.text
    assert 'Write the release notes' not in (await _board(RESEARCHER, action='read', filter='unclaimed')).text

    # Progress from whoever claimed it keeps it open.
    await _chat(WRITER)
    await _board(WRITER, action='answer', post_id=str(post.id), text='Halfway there.')
    assert (await service.get_post('u1', str(post.id))).status == 'open'

    # Ask and wait: the answer comes back in the same call.
    await _chat(RESEARCHER)

    async def answer_soon():
        await asyncio.sleep(0.2)
        [question] = [p for p in await service.posts_on('u1', 'agents') if p.kind == 'question']
        await service.reply('u1', str(question.id), 'Use the March numbers.',
                            service.Author(kind='agent', id='a-wri', name='Writer'))

    asked, _ = await asyncio.gather(
        _board(RESEARCHER, action='post', kind='question', text='Which numbers go in the notes?', to='Writer',
               seconds=5),
        answer_soon())
    assert 'Use the March numbers.' in asked.text

    timed_out = await _board(RESEARCHER, action='wait', post_id=str(post.id), seconds=1)
    assert 'No answer' in timed_out.text


async def test_people_have_names_and_get_tagged(db, agents, started, inbox):
    from cognitrix.board import channels, service

    owner = await _user('Ama Mensah', 'ama@example.com')
    teammate = await _user('Kofi', 'kofi@example.com')
    pid = await _project(owner)
    board = f'project:{pid}'
    await channels.save_channel(owner, board, members=[teammate])

    # A member reads and posts on the owner's channel under their own name.
    assert await channels.owner_for(teammate, board) == owner
    kofi = await channels.person(teammate)
    post = await service.create_post(owner, board=board, kind='question', text='Is Friday still the date?',
                                     to='Ama Mensah', author=kofi)
    assert post.author_name == 'Kofi' and post.to_name == 'you'
    assert inbox[-1].user_id == owner

    # An agent tags the teammate: their Inbox hears about it.
    await _project_chat(RESEARCHER, owner, pid)
    await _board(RESEARCHER, action='post', kind='finding', text='@Kofi the venue moved to Accra.')
    assert inbox[-1].user_id == teammate and 'tagged you' in inbox[-1].title
    asked = await _board(RESEARCHER, action='post', kind='question', text='Can you confirm the venue?', to='Kofi')
    assert asked.status == 'success', asked.text
    latest = (await service.posts_on(owner, board))[0]
    assert latest.to_id == f'user:{teammate}' and inbox[-1].user_id == teammate

    # Answering as the teammate tells the agent's chat; only they edit their own post.
    with pytest.raises(service.BoardError):
        await service.edit_post(owner, str(post.id), 'changed', await channels.person(owner))
    edited = await service.edit_post(owner, str(post.id), 'Is Friday the date?', kofi)
    assert edited.text == 'Is Friday the date?'
    assert service.public_post(edited, teammate)['mine'] and not service.public_post(edited, owner)['mine']

    with pytest.raises(service.BoardError):
        await channels.owner_for(await _user('Stranger', 's@example.com'), board)


async def test_outside_agents_join_with_an_inbox(db, agents, started):
    from cognitrix.board import channels, service

    owner = 'u1'
    bot = await channels.register_external(owner, 'Codex')
    assert bot.kind == 'external' and bot.id == 'ext:Codex'
    post = await service.create_post(owner, board='agents', kind='question', text='Who owns the deploy script?',
                                     author=bot)
    assert post.author_name == 'Codex' and not started  # nobody named: no chat started

    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='answer', post_id=str(post.id), text='The Writer does. @Codex ask them.')
    await _board(RESEARCHER, action='post', kind='request', text='Run the linter on main.', to='Codex')
    found = await channels.inbox(owner, bot)
    assert [p.text for p in found['for_you']] == ['Run the linter on main.']
    assert [str(p.id) for p in found['answers']] == [str(post.id)]
    assert not started  # outside agents read their inbox, nothing is started for them
    again = await channels.inbox(owner, bot)
    assert not again['answers'] and again['for_you']  # read now; open requests stay listed

    pid = await _project(owner)
    with pytest.raises(service.BoardError) as refused:
        await service.create_post(owner, board=f'project:{pid}', kind='finding', text='hi', author=bot)
    assert refused.value.code == 'not_granted'
    await channels.save_channel(owner, f'project:{pid}', externals=['Codex'])
    assert (await service.create_post(owner, board=f'project:{pid}', kind='finding', text='hi', author=bot)).id


async def test_scheduled_posts_wake_the_agent(db, agents, started):
    from cognitrix.board import schedules, service

    assert schedules.parse_when('weekdays 09:00') == ([0, 1, 2, 3, 4], '09:00')
    assert schedules.parse_when('every friday at 4:15 pm') == ([4], '16:15')
    assert schedules.parse_when('mon,thu 9am') == ([0, 3], '09:00')
    with pytest.raises(service.BoardError):
        schedules.parse_when('sometimes')

    row = await schedules.save_schedule('u1', board='agents', text='Check open PRs.', to='Writer',
                                        when='daily 09:00', tz='UTC')
    due = datetime.fromtimestamp(row.next_at / 1000, UTC) + timedelta(seconds=30)
    assert await schedules.run_due(due, force=True) == 1
    [post] = await service.posts_on('u1', 'agents')
    assert post.kind == 'request' and post.to_name == 'Writer' and 'scheduled' in post.tags
    await service.settle()
    assert started and started[0].agent.id == 'a-wri'
    fresh = (await schedules.list_schedules('u1'))[0]
    assert fresh.next_at > row.next_at and fresh.last_post == str(post.id)
    assert await schedules.run_due(due, force=True) == 0  # not twice

    await _chat(RESEARCHER)
    made = await _board(RESEARCHER, action='schedule', text='Post the weekly numbers.', when='mon 8:00')
    assert made.status == 'success' and 'Mon at 08:00' in made.text
    with pytest.raises(service.BoardError):
        await schedules.save_schedule('u1', board='agents', text='x', to='Nobody', when='daily 9:00')


async def test_commands_run_a_saved_workflow_and_report_back(db, agents, started):
    from cognitrix.board import commands, service

    await commands.save_command('u1', 'review', 'a-wri', '')
    assert (await commands.list_commands('u1'))[0].prompt.startswith('Review the change')

    post = await service.create_post('u1', board='agents', kind='heads_up', text='/review PR 12 please')
    assert post.kind == 'request' and post.status == 'open' and 'review' in post.tags
    assert post.wake['command'] == 'review'
    [call] = started
    assert call.agent.id == 'a-wri' and 'PR 12 please' in call.text and 'Review the change' in call.text

    await call.on_done('complete', 'Two bugs:\n1. off by one\n2. no test')
    fresh = await service.get_post('u1', str(post.id))
    assert fresh.status == 'done' and fresh.replies[-1]['text'].startswith('Two bugs:\n1.')

    # Agents trigger them too; an unknown /word is just a post.
    await _chat(RESEARCHER)
    assert (await _board(RESEARCHER, action='post', text='/review the plan')).status == 'success'
    await service.settle()
    assert len(started) == 2
    plain = await service.create_post('u1', board='agents', kind='finding', text='/etc/hosts was wrong')
    assert plain.kind == 'finding' and not plain.wake


class FakeTransport:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, buttons=None, thread_id=''):
        self.sent.append((chat_id, text, thread_id))
        return f'ts{len(self.sent)}'


async def test_slack_mirror_both_ways(db, agents, started, monkeypatch):
    from cognitrix.board import mirror, service
    from cognitrix.channels.base import Incoming
    from cognitrix.models.chat_channel import ChatChannel

    with pytest.raises(service.BoardError):
        await mirror.add_mirror('u1', 'agents', 'C0123ABCD')
    app = ChatChannel(user_id='u1', kind='slack', peer_id='U1', chat_id='D1')
    await app.save()
    row = await mirror.add_mirror('u1', 'agents', '<#C0123ABCD|launch>')
    assert row.slack_channel == 'C0123ABCD'
    transport = FakeTransport()
    monkeypatch.setattr(mirror, '_transport', lambda channel_id: transport)

    post = await service.create_post('u1', board='agents', kind='question', text='Ship on Friday?')
    await service.settle()
    assert transport.sent[0][0] == 'C0123ABCD' and 'Ship on Friday?' in transport.sent[0][1]
    assert (await service.get_post('u1', str(post.id))).mirror == {str(row.id): 'ts1'}

    await service.reply('u1', str(post.id), 'Yes.', service.Author(kind='agent', id='a-wri', name='Writer'))
    await service.settle()
    assert transport.sent[1][2] == 'ts1' and 'Writer' in transport.sent[1][1]

    # From Slack: a reply in the post's thread is an answer, not sent back.
    taken = await mirror.incoming(app, Incoming(chat_id='C0123ABCD', sender_id='U1', sender_name='', text='Agreed',
                                                private=False, thread_id='ts1', message_id='ts9'))
    await service.settle()
    assert taken and (await service.get_post('u1', str(post.id))).replies[-1]['text'] == 'Agreed'
    assert len(transport.sent) == 2

    # A new message is a new post, linked to its Slack message.
    await mirror.incoming(app, Incoming(chat_id='C0123ABCD', sender_id='U1', sender_name='', text='Who has the keys?',
                                        private=False, message_id='ts10'))
    await service.settle()
    newest = (await service.posts_on('u1', 'agents'))[0]
    assert newest.kind == 'question' and newest.mirror == {str(row.id): 'ts10'} and len(transport.sent) == 2

    # Someone else in the channel is ignored unless the app answers others; other channels pass through.
    assert await mirror.incoming(app, Incoming(chat_id='C0123ABCD', sender_id='U2', sender_name='', text='hi',
                                               private=False))
    assert len(await service.posts_on('u1', 'agents')) == 2
    assert not await mirror.incoming(app, Incoming(chat_id='C999', sender_id='U1', sender_name='', text='hi',
                                                   private=False))


async def test_task_result_posts_in_its_thread(db, agents, started):
    from cognitrix.board import service
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    owner = await _user('Ama', 'ama@example.com')
    pid = await _project(owner)
    task = Task(title='Count the signups', description='', owner_id=owner, project_id=pid, assigned_agents=['a-res'])
    await task.save()
    run = TaskRun(task_id=str(task.id), status=TaskRunStatus.COMPLETED, result='412 signups this week.')
    await run.save()
    assert await service.post_task_result(str(run.id)) is None  # nobody uses the thread: nothing posted

    await service.create_post(owner, board=f'project:{pid}', kind='request', text='How many signups?',
                              thread=f'task:{task.id}')
    posted = await service.post_task_result(str(run.id))
    assert posted is not None and posted.thread == f'task:{task.id}' and 'result' in posted.tags
    assert posted.author_name == 'Researcher' and 'finished' in posted.text
    assert await service.post_task_result(str(run.id)) is None  # once per run


async def test_outside_agent_over_http_with_a_board_key(db, agents, started):
    import httpx
    from fastapi import FastAPI

    from cognitrix.api.routes.board import board_api
    from cognitrix.board import channels, service
    from cognitrix.models.api_key import APIKey, generate_secret, hash_secret

    owner = await _user('Ama', 'ama@example.com')
    pid = await _project(owner)

    async def key(scopes):
        secret = generate_secret()
        await APIKey(name='Codex', user_id=owner, key_hash=hash_secret(secret), prefix=secret[:12],
                     scopes=scopes).save()
        return {'Authorization': f'Bearer {secret}'}

    app = FastAPI()
    app.include_router(board_api, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
        wrong = await http.get('/api/board/boards', headers=await key(['read']))
        assert wrong.status_code == 403
        headers = {**await key(['board']), 'X-Board-Name': 'Codex CLI'}
        assert (await http.get('/api/board/me', headers=headers)).json()['name'] == 'Codex CLI'
        made = await http.post('/api/board/posts', headers=headers,
                               json={'board': 'agents', 'kind': 'request', 'text': 'Bump the version.'})
        assert made.status_code == 200 and made.json()['author_name'] == 'Codex CLI'
        post_id = made.json()['id']
        assert (await http.post(f'/api/board/posts/{post_id}/claim', headers=headers)).json()['claimed_by']['name'] \
            == 'Codex CLI'
        # Not added to the project's channel yet; settings stay with the signed-in owner.
        refused = await http.post('/api/board/posts', headers=headers,
                                  json={'board': f'project:{pid}', 'kind': 'finding', 'text': 'hi'})
        assert refused.status_code == 403
        assert (await http.put('/api/board/settings', headers=headers, json={'enabled': False})).status_code == 403
        await channels.save_channel(owner, f'project:{pid}', externals=['Codex CLI'])
        assert (await http.post('/api/board/posts', headers=headers,
                                json={'board': f'project:{pid}', 'kind': 'finding', 'text': 'hi'})).status_code == 200
        await service.reply(owner, post_id, 'Thanks, merged.', await channels.person(owner))
        box = (await http.get('/api/board/inbox', headers=headers)).json()
        assert [p['id'] for p in box['answers']] == [post_id]
        waited = (await http.get(f'/api/board/posts/{post_id}/wait', headers=headers,
                                 params={'seconds': 1, 'seen': 0})).json()
        assert waited['new_replies'][0]['text'] == 'Thanks, merged.'
        scheduled = await http.post('/api/board/schedules', headers=headers,
                                    json={'board': 'agents', 'text': 'Check CI.', 'when': 'weekdays 08:00'})
        assert scheduled.status_code == 200 and scheduled.json()['created_by'] == 'Codex CLI'


def test_board_cli_turns_actions_into_calls():
    from types import SimpleNamespace as NS

    from cognitrix.board import cli

    calls = []

    class Fake:
        def call(self, method, path, **kw):
            calls.append((method, path, kw))
            if path == '/posts' and method == 'POST':
                return {'id': 'p1', 'status': 'open', 'kind': 'question', 'text': kw['json']['text']}
            return {'post': {'id': 'p1'}, 'new_replies': [{'author_name': 'Ama', 'text': 'main'}],
                    'timed_out': False}

    base = dict(board='project:x', kind='', to='Ama', tags='', thread='task:t1', filter='', wait=30, seconds=120,
                close=False, peek=False, when='')
    out = cli.run(NS(action='ask', args=['Which', 'branch?'], **base), Fake())
    assert calls[0][2]['json'] == {'board': 'project:x', 'kind': 'question', 'text': 'Which branch?', 'to': 'Ama',
                                   'tags': '', 'thread': 'task:t1'}
    assert calls[1][1] == '/posts/p1/wait' and cli.show('ask', out) == 'Ama: main'
    cli.run(NS(action='claim', args=['p9'], **base), Fake())
    assert calls[-1][:2] == ('POST', '/posts/p9/claim')
    with pytest.raises(SystemExit):
        cli.run(NS(action='schedule', args=['x'], **base), Fake())
