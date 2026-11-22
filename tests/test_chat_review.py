"""Chat review: the nightly pass that learns from finished chats and task runs.

- Finished chats are read once; busy, incognito and short chats are left alone.
- Facts become memories (proposals under "Ask me first"), a completed job
  becomes a personal skill, and reference material becomes a knowledge note
  the working agent can search.
- An existing skill is improved instead of duplicated, and Remove takes back
  a new item or puts an improved one back as it was.
- Completed task runs are reviewed too.
- Nightly runs share dream mode's window, and dream mode waits for a review.
"""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_saved_memories import _ctx, _user, db  # noqa: F401

LONG_JOB = [
    {'role': 'user', 'content': 'Pull last month\'s invoices from the billing portal and total them by client.'},
    {'role': 'assistant', 'content': 'Opening the billing portal export.',
     'tool_calls': [{'function': {'name': 'browser_open'}}]},
    {'role': 'tool', 'content': 'CSV with 42 rows: client, amount, date ' * 10},
    {'role': 'assistant', 'content': 'Totals by client: Acme 4,200; Globex 1,900. ' * 5},
    {'role': 'user', 'content': 'Great, and always round to whole cedis please.'},
    {'role': 'assistant', 'content': 'Done, rounded to whole cedis. ' * 5},
]

SKILL_BODY = ('## When to use\nMonthly invoice totals.\n\n## Steps\n1. Open the billing portal export.\n'
              '2. Download the CSV.\n3. Total by client, rounded to whole cedis.\n\n## Output\nA table by client.')


class _LLM:
    provider = 'test'
    model = 'test-model'

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, messages, stream=False):
        self.prompts.append(messages)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(llm_response=json.dumps(reply), error=None)


def _reply(**over):
    base = {'summary': 'Totalled invoices by client.', 'completed': True, 'facts': [], 'skill': None,
            'knowledge': None}
    base.update(over)
    return base


async def _chat(user, chat, **fields):
    from cognitrix.sessions.base import Session

    row = Session(user_id=str(user.id), chat=chat, **fields)
    await row.save()
    return row


def _later(hours=5):
    """A review time this long after the chats were last touched."""
    return datetime.now(UTC) + timedelta(hours=hours)


@pytest.fixture
async def notes(monkeypatch):
    """Knowledge tables, with passages kept in memory instead of Chroma."""
    from cognitrix.knowledge import store
    from cognitrix.knowledge.models import KnowledgeCollection, KnowledgeDocument

    await KnowledgeCollection._create_table_async()
    await KnowledgeDocument._create_table_async()
    kept = {}

    async def add(collection_id, document_id, name, pieces):
        kept[document_id] = '\n'.join(pieces)

    async def remove_document(collection_id, document_id):
        kept.pop(document_id, None)

    monkeypatch.setattr(store, 'add', add)
    monkeypatch.setattr(store, 'remove_document', remove_document)
    return kept


async def test_a_finished_chat_becomes_a_skill_facts_and_a_note(db, notes):  # noqa: F811
    from cognitrix.memory import review, saved
    from cognitrix.skills import personal

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    done = await _chat(user, LONG_JOB)
    await _chat(user, LONG_JOB, incognito=True)
    await _chat(user, [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'Hello!'}])

    llm = _LLM(_reply(
        facts=[{'content': 'Wants money rounded to whole cedis.', 'kind': 'preference', 'evidence': 'always round'}],
        skill={'update': '', 'name': 'monthly-invoice-totals', 'description': 'Total last month\'s invoices by client.',
               'body': SKILL_BODY},
        knowledge={'title': 'Billing portal', 'content': 'The billing portal export lives under Reports > Export CSV. '
                                                         'Columns: client, amount, date.'},
    ))
    busy = await review.run_review(uid, 'night', llm=llm)
    assert busy.status == review.DONE and llm.prompts == [], 'chats touched in the last hours are still going'

    run = await review.run_review(uid, 'night', llm=llm, now=_later())
    assert run.status == review.DONE, run.error
    assert len(llm.prompts) == 1, 'only the finished, long, non-incognito chat is read'
    assert 'billing portal' in llm.prompts[0][1]['content']

    skills = await personal.skills_of(uid)
    assert [s.name for s in skills] == ['monthly-invoice-totals'] and skills[0].author == review.AUTHOR
    assert [m.content for m in await saved.list_memories(uid)] == ['Wants money rounded to whole cedis.']
    assert any('Reports > Export CSV' in text for text in notes.values())

    learned = await review.learned_for(uid)
    assert sorted(r.kind for r in learned) == ['knowledge', 'memory', 'skill']
    assert all(r.source_id == str(done.id) and r.source_link == f'/home?chat={done.id}' for r in learned)
    assert 'wrote 1 new skill' in run.summary and '1 fact' in run.summary

    # Each chat is read once...
    again = await review.run_review(uid, 'manual', llm=llm, now=_later(6))
    assert len(llm.prompts) == 1 and 'No finished chats' in again.summary
    # ...until it grows, and then only the new part.
    from cognitrix.sessions.base import Session

    more = [{'role': 'user' if i % 2 == 0 else 'assistant',
             'content': f'Now the same totals for the Lagos office, part {i}, with the new client list. ' * 2}
            for i in range(6)]
    await Session.update_one({'id': done.id}, {'chat': LONG_JOB + more})
    grown = _LLM(_reply())
    await review.run_review(uid, 'manual', llm=grown, now=_later(7))
    work = grown.prompts[0][1]['content'].split('</work>')[0].split('">', 1)[1]
    assert 'Lagos office' in work and 'billing portal' not in work


async def test_existing_skill_is_improved_and_undo_puts_it_back(db, notes):  # noqa: F811
    from cognitrix.memory import review
    from cognitrix.skills import personal

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    old = await personal.create(uid, 'monthly-invoice-totals', 'Total last month\'s invoices by client.',
                                '## Steps\n1. Ask the person for the invoices and add them up by client.')
    await _chat(user, LONG_JOB)
    llm = _LLM(_reply(skill={'update': 'monthly-invoice-totals', 'name': 'invoice-totals-v2',
                             'description': 'Total last month\'s invoices by client.', 'body': SKILL_BODY}))
    run = await review.run_review(uid, 'manual', llm=llm, now=_later())
    assert 'monthly-invoice-totals' in llm.prompts[0][1]['content'].split('<related_skills>')[1]
    skills = await personal.skills_of(uid)
    assert [s.name for s in skills] == ['monthly-invoice-totals'] and skills[0].body == SKILL_BODY
    assert 'improved 1 skill' in run.summary

    item = (await review.learned_for(uid))[0]
    assert item.action == 'updated'
    await review.remove(item)
    assert (await personal.skills_of(uid))[0].body == old.body
    with pytest.raises(review.RemoveError):
        await review.remove(await review.LearnedItem.get(item.id))


async def test_duplicates_and_secrets_are_not_saved(db, notes):  # noqa: F811
    from cognitrix.memory import review
    from cognitrix.skills import personal

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await personal.create(uid, 'monthly-invoice-totals', 'Total last month\'s invoices by client.',
                          '## Steps\n1. Something entirely different that still has enough words.')
    await _chat(user, LONG_JOB)
    await _chat(user, LONG_JOB)
    llm = _LLM(
        # Same description as the existing skill, under a new name: a duplicate.
        _reply(skill={'name': 'invoice-totals', 'description': 'Total last month\'s invoices by client.',
                      'body': SKILL_BODY}),
        _reply(skill={'name': 'portal-login', 'description': 'Sign in to the billing portal.',
                      'body': SKILL_BODY + '\npassword: hunter22'},
               facts=[{'content': 'My API key is sk-abcdefghijklmnopqrstuvwxyz123456'}]),
    )
    run = await review.run_review(uid, 'manual', llm=llm, now=_later())
    assert run.status == review.DONE
    assert [s.name for s in await personal.skills_of(uid)] == ['monthly-invoice-totals']
    assert await review.learned_for(uid) == []
    assert 'Nothing new to keep' in run.summary


async def test_ask_first_makes_facts_proposals_and_remove_deletes(db, notes):  # noqa: F811
    from cognitrix.memory import review, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await saved.save_settings(uid, auto_save='ask')
    await _chat(user, LONG_JOB)
    llm = _LLM(_reply(completed=False, facts=[{'content': 'Works with Acme and Globex.', 'kind': 'project'}],
                      skill={'name': 'x-y', 'description': 'Not completed, so no skill.', 'body': SKILL_BODY}))
    await review.run_review(uid, 'manual', llm=llm, now=_later())
    item = (await review.learned_for(uid))[0]
    assert item.kind == 'memory' and item.action == 'proposed'
    pending = await saved.pending_memories(uid)
    assert [m.content for m in pending] == ['Works with Acme and Globex.']
    await review.remove(item)
    assert await saved.pending_memories(uid) == []


async def test_completed_task_runs_are_reviewed(db, notes):  # noqa: F811
    from cognitrix.memory import review
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun, TaskRunStatus, utc_now

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    task = Task(title='Invoice totals', description='Total last month\'s invoices by client.', owner_id=uid)
    await task.save()
    task = await Task.find_one({'title': 'Invoice totals'})
    run = TaskRun(task_id=str(task.id), status=TaskRunStatus.COMPLETED, result='Acme 4,200; Globex 1,900',
                  plan=[{'title': 'Export the CSV'}, {'title': 'Total by client'}], completed_at=utc_now())
    await run.save()
    run = await TaskRun.find_one({'task_id': str(task.id)})
    await Session(chat=LONG_JOB, task_id=str(task.id), run_id=str(run.id), step_index=0, step_title='Export',
                  user_id=uid).save()
    other = TaskRun(task_id=str(task.id), status=TaskRunStatus.FAILED, completed_at=utc_now())
    await other.save()

    llm = _LLM(_reply(skill={'name': 'monthly-invoice-totals', 'description': 'Total invoices by client.',
                             'body': SKILL_BODY}))
    result = await review.run_review(uid, 'manual', llm=llm, now=_later())
    assert result.status == review.DONE
    assert len(llm.prompts) == 1, 'the step session is part of the task run, not a chat of its own'
    prompt = llm.prompts[0][1]['content']
    assert 'Task: Invoice totals' in prompt and 'Export the CSV' in prompt and 'Acme 4,200' in prompt
    learned = await review.learned_for(uid)
    assert learned[0].source_kind == 'task' and learned[0].source_title == 'Invoice totals'


async def test_bad_reply_marks_the_chat_and_fails_cleanly(db, notes):  # noqa: F811
    from cognitrix.memory import review

    user = await _user('Amos', 'amos@example.com')
    await _chat(user, LONG_JOB)

    class Broken(_LLM):
        async def __call__(self, messages, stream=False):
            return SimpleNamespace(llm_response='no json here', error=None)

    run = await review.run_review(str(user.id), 'manual', llm=Broken({}), now=_later())
    assert run.status == review.FAILED


async def test_review_routes_belong_to_their_owner(db, notes, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import memories as routes
    from cognitrix.memory import review

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    started = []

    async def fake_start(user_id, trigger='manual'):
        started.append((user_id, trigger))
        row = review.ReviewRun(user_id=user_id, trigger=trigger, status=review.RUNNING, started=review.now_str())
        await row.save()
        return await review.ReviewRun.find_one({'user_id': user_id})

    monkeypatch.setattr(review, 'start', fake_start)
    result = await routes.review_now(_ctx(amos))
    assert started == [(str(amos.id), 'manual')] and result['status'] == review.RUNNING
    status = await routes.review_status(_ctx(amos))
    assert status['running'] is True and status['enabled'] is True and status['learned'] == []
    assert (await routes.review_status(_ctx(bea)))['runs'] == []

    await _chat(amos, LONG_JOB)
    await review.run_review(str(amos.id), 'manual', llm=_LLM(_reply(
        facts=[{'content': 'Works with Acme and Globex.', 'kind': 'project'}])), now=_later())
    item = (await routes.review_status(_ctx(amos)))['learned'][0]
    with pytest.raises(HTTPException):
        await routes.remove_learned(item['id'], _ctx(bea))
    removed = await routes.remove_learned(item['id'], _ctx(amos))
    assert removed['removed'] is True

    settings = await routes.put_settings(routes.SettingsBody(review_enabled=False), _ctx(amos))
    assert settings['review_enabled'] is False


async def test_nightly_window_and_dream_waits(db):  # noqa: F811
    from cognitrix.memory import dream, review, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    night = datetime(2026, 11, 22, 3, 30, tzinfo=UTC)
    noon = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    assert await review.review_due(uid, night)
    assert not await review.review_due(uid, noon)
    await saved.save_settings(uid, review_enabled=False)
    assert not await review.review_due(uid, night)
    await saved.save_settings(uid, review_enabled=True)

    await saved.add(uid, 'Likes tea.')
    assert await dream.dream_due(uid, night)
    row = review.ReviewRun(user_id=uid, trigger='night', status=review.RUNNING, started=review.now_str())
    await row.save()
    assert not await dream.dream_due(uid, night), 'dream mode waits for the review'
    assert not await review.review_due(uid, night)


async def test_leftover_work_waits_for_the_next_review(db, notes, monkeypatch):  # noqa: F811
    from cognitrix.memory import review

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    monkeypatch.setattr(review, 'ITEMS_PER_RUN', 1)
    await _chat(user, LONG_JOB)
    await _chat(user, LONG_JOB)
    llm = _LLM(_reply())
    first = await review.run_review(uid, 'night', llm=llm, now=_later())
    assert len(llm.prompts) == 1 and '1 more wait' in first.summary
    await review.run_review(uid, 'night', llm=llm, now=_later(30))
    assert len(llm.prompts) == 2, 'the chat left over is read the next night'


async def test_removing_an_older_note_after_an_update(db, notes):  # noqa: F811
    from cognitrix.memory import review

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await _chat(user, LONG_JOB)
    await _chat(user, LONG_JOB)
    note = {'title': 'Billing portal', 'content': 'The billing portal export lives under Reports > Export CSV. ' * 2}
    newer = dict(note, content='The export moved to Data > Downloads > Invoices CSV in November. ' * 2)
    await review.run_review(uid, 'manual', llm=_LLM(_reply(knowledge=note), _reply(knowledge=newer)), now=_later())
    items = sorted(await review.learned_for(uid), key=lambda r: r.action)
    assert [r.action for r in items] == ['created', 'updated']
    await review.remove(items[1])  # undo the update: the first text is back
    assert any('Reports > Export CSV' in t for t in notes.values())
    await review.remove(items[0])  # and the original can still be removed
    assert notes == {}
