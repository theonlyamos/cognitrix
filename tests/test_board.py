"""The message board: posts, answers, the start-of-turn note, wakes and limits."""
from types import SimpleNamespace

import pytest

from cognitrix.tools.utils import ToolExecutionContext, set_execution_context


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'board.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path / 'data')
    (tmp_path / 'data').mkdir()
    yield tmp_path
    set_execution_context(ToolExecutionContext())


class FakeAgent(SimpleNamespace):
    pass


RESEARCHER = FakeAgent(id='a-res', name='Researcher', owner_id='u1')
WRITER = FakeAgent(id='a-wri', name='Writer', owner_id='u1')


@pytest.fixture
def agents(monkeypatch):
    import cognitrix.agents.access as access

    found = {a.id: a for a in (RESEARCHER, WRITER)}

    async def visible(user_key):
        return list(found.values())

    async def find(user_key, *, agent_id=None, name=None):
        if agent_id:
            return found.get(agent_id)
        return next((a for a in found.values() if a.name == name), None)

    monkeypatch.setattr(access, 'visible_agents', visible)
    monkeypatch.setattr(access, 'find_visible_agent', find)
    return found


@pytest.fixture
def started(monkeypatch):
    """Chats the board starts to wake an agent: recorded, not run."""
    from cognitrix.sessions import agent_chats

    calls = []

    async def create_chat(user_key, agent, started_by, folders=None):
        from cognitrix.sessions.base import Session

        session = Session(agent_id=str(agent.id), user_id=user_key, started_by=started_by)
        await session.save()
        return session

    def run_in_background(user_key, agent, session, text, *, on_done=None, **kw):
        calls.append(SimpleNamespace(agent=agent, session=session, text=text, on_done=on_done))

    monkeypatch.setattr(agent_chats, 'create_chat', create_chat)
    monkeypatch.setattr(agent_chats, 'run_in_background', run_in_background)
    return calls


async def _chat(agent, user='u1'):
    from cognitrix.sessions.base import Session

    session = Session(agent_id=agent.id, user_id=user)
    await session.save()
    set_execution_context(ToolExecutionContext(user_id=user, session_id=str(session.id), agent_id=agent.id))
    return session


async def _board(agent, **params):
    from cognitrix.tools.board import board

    return (await board.run(parent=agent, **params)).outcome


async def test_agents_post_read_answer_and_close(db, agents, started):
    from cognitrix.board import service

    await _chat(RESEARCHER)
    found = await _board(RESEARCHER, action='post', kind='finding', text='Acme raised prices to $49.',
                         tags='pricing, #Acme')
    assert found.status == 'success', found.text
    asked = await _board(RESEARCHER, action='post', kind='question', text='Do we quote prices with VAT?')
    assert 'told here when it is answered' in asked.text

    posts = await service.posts_on('u1', 'agents')
    assert [p.kind for p in posts] == ['question', 'finding']
    assert posts[1].tags == ['pricing', 'acme'] and posts[0].status == 'open'
    question = posts[0]

    await _chat(WRITER)
    listed = (await _board(WRITER, action='read', filter='open')).text
    assert 'Do we quote prices with VAT?' in listed and 'Acme' not in listed
    assert 'Acme' in (await _board(WRITER, action='read', filter='#acme')).text

    answer = await _board(WRITER, action='answer', post_id=str(question.id), text='Without VAT.')
    assert answer.status == 'success'
    fresh = await service.get_post('u1', str(question.id))
    assert fresh.status == 'answered' and fresh.replies[0]['author_name'] == 'Writer'
    assert (await _board(WRITER, action='close', post_id=str(question.id))).status == 'success'
    assert (await service.get_post('u1', str(question.id))).status == 'done'

    bad = await _board(WRITER, action='post', kind='gossip', text='x')
    assert bad.error.code == 'bad_kind'
    assert (await _board(WRITER, action='post', kind='finding', text='')).error.code == 'text_required'
    assert (await _board(WRITER, action='post', kind='question', text='?', to='Nobody')).error.code == 'bad_to'


async def test_api_keys_and_other_people_are_kept_out(db, agents):
    from cognitrix.board import service

    set_execution_context(ToolExecutionContext(user_id='u1', api_key_id='k1', session_id='s'))
    assert (await _board(RESEARCHER, action='read')).error.code == 'not_allowed'
    post = await service.create_post('u1', board='agents', kind='finding', text='mine')
    with pytest.raises(service.BoardError):
        await service.get_post('u2', str(post.id))
    assert await service.posts_on('u2', 'agents') == []


async def test_a_question_for_the_person_goes_to_their_inbox(db, agents):
    from cognitrix.board import service
    from cognitrix.tasks.automation import AgentNotification

    await _chat(RESEARCHER)
    outcome = await _board(RESEARCHER, action='post', kind='question', text='Which currency?', to='you')
    assert 'Inbox' in outcome.text
    alerts = await AgentNotification.find({'user_id': 'u1'})
    assert len(alerts) == 1 and alerts[0].link.startswith('/board?board=agents&post=')

    post = (await service.posts_on('u1', 'agents'))[0]
    await service.reply('u1', str(post.id), 'Cedis.', service.PERSON)
    assert (await service.get_post('u1', str(post.id))).status == 'answered'


async def test_a_question_for_an_agent_wakes_it_and_its_reply_is_the_answer(db, agents, started):
    from cognitrix.board import service

    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='request', text='Write the pricing blurb.', to='writer')
    await service.settle()
    assert len(started) == 1
    call = started[0]
    assert call.agent is WRITER and 'Board (action "answer"' in call.text
    assert call.session.started_by['via'] == 'board' and call.session.started_by['depth'] == 1
    post = (await service.posts_on('u1', 'agents'))[0]
    assert post.wake['state'] == 'sent' and post.to_name == 'Writer'

    await call.on_done('complete', 'Done: "Simple prices, no surprises."')
    post = await service.get_post('u1', str(post.id))
    assert post.status == 'answered' and post.replies[0]['author_name'] == 'Writer'


async def test_wakes_have_a_daily_limit_and_can_be_turned_off(db, agents, started):
    from cognitrix.board import service

    await service.save_settings('u1', {'wakes_per_day': 1})
    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='question', text='First?', to='Writer')
    await _board(RESEARCHER, action='post', kind='question', text='Second?', to='Writer')
    await service.settle()
    assert len(started) == 1
    second = next(p for p in await service.posts_on('u1', 'agents') if p.text == 'Second?')
    assert second.wake['state'] == 'skipped' and 'limit' in second.wake['reason']
    assert service.public_settings(await service.get_settings('u1'))['wakes_today'] == 1

    await service.save_settings('u1', {'wakes_per_day': 5})
    await service.save_follow('u1', WRITER.id, wake=False)
    await _board(RESEARCHER, action='post', kind='question', text='Third?', to='Writer')
    await service.settle()
    assert len(started) == 1

    # Too deep in agent-started chats: no new chat.
    await service.save_follow('u1', WRITER.id, wake=True)
    deep = service.Author(kind='agent', id=RESEARCHER.id, name='Researcher', depth=2)
    await service.create_post('u1', board='agents', kind='question', text='Deep?', to='Writer', author=deep)
    await service.settle()
    assert len(started) == 1

    with pytest.raises(service.BoardError):
        await service.save_settings('u1', {'wakes_per_day': 1000})


async def test_the_turn_note_lists_what_concerns_the_agent_once(db, agents, started):
    from cognitrix.board import service

    researcher = service.Author(kind='agent', id=RESEARCHER.id, name='Researcher')
    await service.create_post('u1', board='agents', kind='decision', text='Prices are always ex-VAT.',
                              author=researcher)
    await service.create_post('u1', board='agents', kind='finding', text='Acme is down.', tags=['acme'],
                              author=researcher)
    await service.create_post('u1', board='agents', kind='question', text='Writer, which tone?', to='Writer',
                              author=researcher)
    await service.settle()

    note = await service.digest('u1', WRITER.id, 'Writer')
    assert note.startswith(service.DIGEST_HEADER)
    assert 'For you:' in note and 'Writer, which tone?' in note
    assert 'Pinned decisions:' in note and 'ex-VAT' in note
    assert 'Acme is down' not in note  # findings aren't followed by default

    again = await service.digest('u1', WRITER.id, 'Writer')
    assert 'Writer, which tone?' in again and 'New since you last looked' not in again

    await service.save_follow('u1', WRITER.id, tags=['acme'])
    await service.create_post('u1', board='agents', kind='finding', text='Acme is back.', tags=['acme'],
                              author=researcher)
    note = await service.digest('u1', WRITER.id, 'Writer')
    assert 'New since you last looked:' in note and 'Acme is back' in note

    # Its own posts aren't listed back to the author.
    assert 'which tone' not in await service.digest('u1', RESEARCHER.id, 'Researcher')

    await service.save_settings('u1', {'enabled': False})
    assert await service.digest('u1', WRITER.id, 'Writer') == ''
    set_execution_context(ToolExecutionContext(user_id='u1', agent_id=WRITER.id))
    assert (await _board(WRITER, action='post', kind='finding', text='x')).error.code == 'board_off'


async def test_follow_through_the_tool(db, agents):
    await _chat(WRITER)
    shown = (await _board(WRITER, action='follow')).text
    assert 'Decision, Question, Request' in shown
    saved = (await _board(WRITER, action='follow', kinds='finding, decision', tags='launch', wake='no')).text
    assert saved.startswith('Saved.') and 'Finding, Decision' in saved and '#launch' in saved
    assert 'wait until you next work' in saved


async def test_old_posts_drop_off_unless_pinned_or_open(db, agents):
    from cognitrix.board import service
    from cognitrix.board.models import BoardPost

    for kind in ('finding', 'decision', 'question'):
        post = await service.create_post('u1', board='agents', kind=kind, text=f'old {kind}')
        await BoardPost.update_one({'id': post.id}, {'posted_at': 1577836800000,
                                                     'changed_at': 1577836800000})
    await service.create_post('u1', board='agents', kind='finding', text='new')
    texts = {p.text for p in await service.posts_on('u1', 'agents')}
    assert texts == {'old decision', 'old question', 'new'}


async def test_boards_list_counts_and_answers_reach_the_asking_chat(db, agents, monkeypatch):
    from cognitrix.board import service
    from cognitrix.sessions import agent_chats

    delivered = []

    async def deliver(user_key, agent_id, chat_id, text, *, sender, **kw):
        delivered.append((agent_id, chat_id, text, sender))
        return 'started'

    monkeypatch.setattr(agent_chats, 'deliver_report', deliver)
    chat = await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='question', text='Anyone know the launch date?')
    post = (await service.posts_on('u1', 'agents'))[0]
    await service.reply('u1', str(post.id), 'March 3.', service.PERSON)
    await service.settle()
    assert delivered and delivered[0][:2] == (RESEARCHER.id, str(chat.id))
    assert 'March 3.' in delivered[0][2] and delivered[0][2].startswith(agent_chats.REPORT_PREFIX)

    rows = await service.boards('u1')
    assert rows[0]['key'] == 'agents' and rows[0]['total'] == 1 and rows[0]['open'] == 0


async def test_answers_and_wakes_at_the_same_time_are_all_kept(db, agents):
    import asyncio

    from cognitrix.board import service

    post = await service.create_post('u1', board='agents', kind='question', text='Both?')
    writer = service.Author(kind='agent', id=WRITER.id, name='Writer')
    await asyncio.gather(service.reply('u1', str(post.id), 'One.', service.PERSON),
                         service.reply('u1', str(post.id), 'Two.', writer))
    assert {r['text'] for r in (await service.get_post('u1', str(post.id))).replies} == {'One.', 'Two.'}

    await service.save_settings('u1', {'wakes_per_day': 1})
    taken = await asyncio.gather(*(service._take_wake('u1') for _ in range(4)))
    assert taken.count(True) == 1


async def test_old_open_and_pinned_posts_stay_in_the_list(db, agents, monkeypatch):
    from cognitrix.board import service

    monkeypatch.setattr(service, 'POSTS_PER_HOUR', 1000)
    await service.create_post('u1', board='agents', kind='question', text='Still open?')
    for i in range(5):
        await service.create_post('u1', board='agents', kind='finding', text=f'f{i}')
    texts = [p.text for p in await service.list_posts('u1', 'agents', limit=2)]
    assert texts[:2] == ['f4', 'f3'] and 'Still open?' in texts and 'f0' not in texts


def test_find_mentions_prefers_longer_names_and_spelled_variants():
    from cognitrix.board.service import find_mentions

    names = [('Research', 'r1'), ('Research Lead', 'r2'), ('Writer', 'w1')]
    found = find_mentions('@research_lead and @Writer, but not me@Writer2 or email@writer.com', names)
    assert [m['name'] for m in found] == ['Research Lead', 'Writer']
    assert find_mentions('@Research did it', names) == [{'name': 'Research', 'id': 'r1'}]
    assert find_mentions('no tags here', names) == []
    lead = find_mentions('ask @Boss', [('lead', 'a9'), ('Accra', 'a1')], lead_name='Boss')
    assert lead == [{'name': 'lead', 'id': 'a9'}]


async def test_tagging_an_agent_in_a_post_or_answer_wakes_it(db, agents, started):
    from cognitrix.board import service

    await _chat(RESEARCHER)
    shown = await _board(RESEARCHER, action='post', kind='finding', text='@Writer the launch moved to May.')
    assert 'Tagged: @Writer' in shown.text and shown.card and shown.card['title'].startswith('Posted a finding')
    await service.settle()
    assert len(started) == 1 and started[0].agent is WRITER and 'tagged you' in started[0].text
    post = (await service.posts_on('u1', 'agents'))[0]
    assert post.mentions == [{'name': 'Writer', 'id': WRITER.id}]
    assert post.mention_wakes[0]['state'] == 'sent'

    # A finding's chat reply isn't saved as an answer (only questions get one).
    await started[0].on_done('complete', 'Noted.')
    assert (await service.get_post('u1', str(post.id))).replies == []

    # Tagging yourself does nothing; tagging in an answer wakes again.
    await _board(RESEARCHER, action='answer', post_id=str(post.id), text='@Researcher @Writer also the price.')
    await service.settle()
    assert len(started) == 2
    fresh = await service.get_post('u1', str(post.id))
    assert fresh.replies[0]['mentions'] == [{'name': 'Writer', 'id': WRITER.id}]

    note = await service.digest('u1', WRITER.id, 'Writer')
    assert 'Tagged you (@):' in note and 'launch moved' in note


async def test_a_tagged_question_gets_the_reply_as_its_answer(db, agents, started):
    from cognitrix.board import service

    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='question', text='@Writer which tone for the launch?')
    await service.settle()
    post = (await service.posts_on('u1', 'agents'))[0]
    await started[0].on_done('complete', 'Warm and plain.')
    fresh = await service.get_post('u1', str(post.id))
    assert fresh.status == 'answered' and fresh.replies[0]['text'] == 'Warm and plain.'


async def test_search_and_the_already_asked_check(db, agents, started):
    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='question', text='Do we quote launch prices with VAT included?')
    await _board(RESEARCHER, action='post', kind='finding', text='Acme raised prices.', tags='acme')

    await _chat(WRITER)
    found = (await _board(WRITER, action='search', query='VAT prices')).text
    assert found.startswith('2 post(s)') and found.index('with VAT') < found.index('Acme')
    assert 'Acme raised' in (await _board(WRITER, action='search', query='#acme')).text
    assert 'Nothing' in (await _board(WRITER, action='search', query='zebra')).text

    dup = await _board(WRITER, action='post', kind='question', text='Are launch prices quoted with VAT included?')
    assert dup.error.code == 'similar_posts' and 'force=true' in dup.error.message
    forced = await _board(WRITER, action='post', kind='question', force=True,
                          text='Are launch prices quoted with VAT included?')
    assert forced.status == 'success'


async def test_edit_and_retract_your_own_post(db, agents, started):
    from cognitrix.board import service

    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='question', text='Which currency?')
    post = (await service.posts_on('u1', 'agents'))[0]

    await _chat(WRITER)
    assert (await _board(WRITER, action='edit', post_id=str(post.id), text='x')).error.code == 'not_yours'

    await _chat(RESEARCHER)
    edited = await _board(RESEARCHER, action='edit', post_id=str(post.id), text='Which currency, @Writer?')
    assert edited.status == 'success' and '@Writer' in edited.text
    await service.settle()
    assert len(started) == 1  # newly tagged on edit
    fresh = await service.get_post('u1', str(post.id))
    assert fresh.edited_at and fresh.text == 'Which currency, @Writer?'

    assert (await _board(RESEARCHER, action='retract', post_id=str(post.id))).status == 'success'
    fresh = await service.get_post('u1', str(post.id))
    assert fresh.retracted and fresh.status == 'done'
    assert 'currency' not in (await _board(WRITER, action='read')).text
    with pytest.raises(service.BoardError):
        await service.reply('u1', str(post.id), 'GHS', service.PERSON)


async def test_unanswered_questions_are_brought_up_once(db, agents, started):
    from datetime import UTC, datetime, timedelta

    from cognitrix.board import service
    from cognitrix.board.models import BoardPost
    from cognitrix.tasks.automation import AgentNotification

    researcher = service.Author(kind='agent', id=RESEARCHER.id, name='Researcher')
    anyone = await service.create_post('u1', board='agents', kind='question', text='Anyone?', author=researcher)
    await service.save_follow('u1', WRITER.id, wake=False)
    to_writer = await service.create_post('u1', board='agents', kind='question', text='Writer?', to='Writer',
                                          author=researcher)
    fresh_one = await service.create_post('u1', board='agents', kind='question', text='New?', author=researcher)
    await service.settle()
    old = int((datetime.now(UTC) - timedelta(hours=30)).timestamp() * 1000)
    for p in (anyone, to_writer):
        await BoardPost.update_one({'id': p.id}, {'posted_at': old})

    await service.save_follow('u1', WRITER.id, wake=True)
    assert await service.nudge_stale(force=True) == 2
    await service.settle()
    assert len(started) == 1 and started[0].agent is WRITER  # it was never woken; now it is
    alerts = await AgentNotification.find({'user_id': 'u1'})
    assert len(alerts) == 1 and 'No answer' in alerts[0].title and 'Anyone?' in alerts[0].message
    assert (await service.get_post('u1', str(fresh_one.id))).nudged_at == 0

    assert await service.nudge_stale(force=True) == 0  # once only
    await service.save_settings('u1', {'nudge_hours': 0})
    assert service.public_settings(await service.get_settings('u1'))['nudge_hours'] == 0


async def test_tags_reach_an_addressed_finding_and_edits_show_in_the_note(db, agents, started):
    from cognitrix.board import service

    await _chat(RESEARCHER)
    await _board(RESEARCHER, action='post', kind='finding', to='Writer', text='@Writer prices changed.')
    await service.settle()
    assert len(started) == 1 and started[0].agent is WRITER

    await service.save_follow('u1', WRITER.id, wake=False)
    await _board(RESEARCHER, action='post', kind='finding', text='Acme is down.')
    post = (await service.posts_on('u1', 'agents'))[0]
    await service.digest('u1', WRITER.id, 'Writer')
    await _board(RESEARCHER, action='edit', post_id=str(post.id), text='Acme is down, @Writer.')
    await service.settle()
    note = await service.digest('u1', WRITER.id, 'Writer')
    assert 'Tagged you (@):' in note and 'Acme is down' in note
