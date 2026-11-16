"""Dream mode and the memory upgrades it builds on.

- Search finds memories by meaning when an embedder is available, and by words otherwise.
- Every change is recorded and can be undone.
- Facts with an end date stop showing once the date passes.
- The "About you" summary leads the prompt block.
- A dream merges exact duplicates, retires expired facts, applies safe
  merges and tags on its own (auto mode), and turns everything else into
  proposals. Pinned memories and the person's own words are never touched
  without asking. A whole dream can be undone.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_saved_memories import _agent, _ctx, _user, db  # noqa: F401


def _fake_embedder(groups):
    """Texts that share a word from the same group point the same way."""
    def embed(texts):
        out = []
        for text in texts:
            low = text.lower()
            vec = [1.0 if any(w in low for w in group) else 0.0 for group in groups]
            vec.append(0.05)
            out.append(vec)
        return out
    return embed


async def _raw(user, content, **fields):
    """A memory row saved directly (no near-duplicate check)."""
    from cognitrix.memory import saved
    from cognitrix.models.memory import Memory

    stamp = fields.pop('created', saved.now_str())
    row = Memory(user_id=str(user.id), content=content, created=stamp, updated=stamp, **fields)
    await row.save()
    return await Memory.find_one({'user_id': str(user.id), 'content': content})


class _LLM:
    provider = 'test'
    model = 'test-small'

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def __call__(self, messages, stream=False):
        self.prompts.append(messages)
        return SimpleNamespace(llm_response=json.dumps(self.reply), error=None)


# ---------------------------------------------------------------- memory upgrades

async def test_search_by_meaning_and_words(db, monkeypatch):  # noqa: F811
    from cognitrix.memory import saved, semantic

    user = await _user('Amos', 'amos@example.com')
    agent = _agent()
    await saved.add(str(user.id), 'Drinks green tea in the morning.')
    await saved.add(str(user.id), 'Works on the Cognitrix project.')

    # Without an embedder only shared words count.
    assert [r.content for r in await saved.search(str(user.id), agent, 'beverage')] == []

    monkeypatch.setenv('COGNITRIX_MEMORY_EMBEDDINGS', '1')
    semantic.set_embedder(_fake_embedder([('tea', 'beverage', 'drink'), ('cognitrix', 'project')]))
    found = await saved.search(str(user.id), agent, 'favourite beverage')
    assert [r.content for r in found] == ['Drinks green tea in the morning.']
    row = (await saved.list_memories(str(user.id)))
    used = next(r for r in row if 'tea' in r.content)
    assert used.use_count == 1 and used.last_used


async def test_history_and_undo(db):  # noqa: F811
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    row, created = await saved.add(str(user.id), 'Lives in Accra.', by='person')
    assert created
    row = await saved.update(row, content='Lives in Kumasi.', by='agent')
    changes = await saved.history(str(user.id))
    assert [c.action for c in changes] == ['update', 'create']
    assert saved.project_change(changes[0])['before']['content'] == 'Lives in Accra.'

    restored = await saved.undo(changes[0])
    assert restored.content == 'Lives in Accra.'
    with pytest.raises(saved.UndoError):
        await saved.undo(await _undone(user))

    await saved.delete(restored, by='person')
    deleted = (await saved.history(str(user.id)))[0]
    assert deleted.action == 'delete'
    back = await saved.undo(deleted)
    assert back.content == 'Lives in Accra.'
    assert [r.content for r in await saved.list_memories(str(user.id))] == ['Lives in Accra.']


async def _undone(user):
    from cognitrix.memory import saved

    return next(c for c in await saved.history(str(user.id)) if c.undone)


async def test_expired_facts_leave_the_prompt(db):  # noqa: F811
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    agent = _agent()
    await saved.add(str(user.id), 'Is travelling to Lagos this week.', valid_until='2020-01-01')
    await saved.add(str(user.id), 'Prefers short answers.', kind='preference')
    block = await saved.prompt_block(str(user.id), agent)
    assert 'Prefers short answers.' in block and 'Lagos' not in block
    rows = {r.content: saved.project(r) for r in await saved.list_memories(str(user.id))}
    assert rows['Is travelling to Lagos this week.']['expired'] is True
    assert rows['Prefers short answers.']['kind'] == 'preference'
    with pytest.raises(saved.MemoryError_):
        saved.clean_valid_until('next tuesday')


async def test_profile_leads_the_prompt_block(db):  # noqa: F811
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    agent = _agent()
    await saved.add(str(user.id), 'Prefers short answers.')
    before = await saved.prompt_block(str(user.id), agent)
    await saved.set_profile(str(user.id), 'Amos builds Cognitrix and likes things brief.', locked=True)
    after = await saved.prompt_block(str(user.id), agent)
    assert after != before
    assert after.index('Amos builds Cognitrix') < after.index('Prefers short answers.')
    profile = await saved.get_profile(str(user.id))
    assert profile.locked


# ---------------------------------------------------------------- dream runs

async def test_dream_auto_mode_tidies_and_proposes(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    a = await _raw(user, 'Likes tea.', created='2026-01-01 00:00:00')
    await _raw(user, 'likes  tea.', created='2026-01-02 00:00:00')            # exact duplicate (agent)
    await _raw(user, 'In Lagos this week.', valid_until='2020-01-01')          # expired
    pinned = await _raw(user, 'Never email clients on Fridays.', pinned=True)
    typed = await _raw(user, 'My sister is Esi.', source='user')
    m1 = await _raw(user, 'Uses pnpm for frontends.', created='2026-01-03 00:00:00')
    m2 = await _raw(user, 'Uses pnpm for frontend work.', created='2026-01-04 00:00:00')

    llm = _LLM({
        'operations': [
            {'op': 'merge', 'ids': [str(m1.id), str(m2.id)], 'content': 'Uses pnpm for frontend projects.',
             'kind': 'howto'},
            {'op': 'tag', 'id': str(a.id), 'kind': 'preference'},
            {'op': 'delete', 'id': str(pinned.id), 'reason': 'old'},              # pinned: ignored
            {'op': 'update', 'id': str(typed.id), 'content': 'Has a sister called Esi.'},
            {'op': 'add', 'content': 'Works in Accra.', 'evidence': 'I work in Accra'},
            {'op': 'delete', 'id': 'made-up'},
        ],
        'profile': 'Amos likes tea and builds frontends with pnpm.',
    })
    run = await dream.run_dream(uid, 'manual', llm=llm)
    assert run.status == dream.DONE, run.error
    stats = dream.stats_of(run)
    assert stats['duplicates'] == 1 and stats['expired'] == 1 and stats['merged'] == 1 and stats['tagged'] == 1
    contents = sorted(r.content for r in await saved.list_memories(uid))
    assert contents == sorted(['Likes tea.', 'Never email clients on Fridays.', 'My sister is Esi.',
                               'Uses pnpm for frontend projects.'])
    assert (await saved.get_memory(uid, str(a.id))).kind == 'preference'
    assert (await saved.get_profile(uid)).text.startswith('Amos likes tea')

    proposals = await dream.proposals_for(uid)
    assert sorted(p.action for p in proposals) == ['add', 'update']
    # The chats and memories go to the model as data, not orders.
    assert 'Never email clients' in llm.prompts[0][-1]['content']

    # Approve the update, reject the add.
    update = next(p for p in proposals if p.action == 'update')
    await dream.decide(update, True)
    assert (await saved.get_memory(uid, str(typed.id))).content == 'Has a sister called Esi.'
    add = next(p for p in proposals if p.action == 'add')
    rejected = await dream.decide(add, False)
    assert rejected.status == dream.PROPOSAL_REJECTED
    with pytest.raises(dream.ProposalError):
        await dream.decide(rejected, True)

    # Undoing the dream brings back what it changed on its own.
    undone = await dream.undo_dream(uid, str(run.id))
    assert undone >= 4
    contents = sorted(r.content for r in await saved.list_memories(uid))
    assert 'Uses pnpm for frontends.' in contents and 'Uses pnpm for frontend work.' in contents
    assert 'In Lagos this week.' in contents
    # The person's own approval is not part of the dream's undo.
    assert 'Has a sister called Esi.' in contents


async def test_dream_ask_mode_only_proposes(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await saved.save_settings(uid, auto_save='ask')
    await _raw(user, 'Likes tea.', created='2026-01-01 00:00:00')
    await _raw(user, 'Likes tea.', created='2026-01-02 00:00:00')
    await saved.set_profile(uid, 'Written by me.', locked=True)
    llm = _LLM({'operations': [], 'profile': 'Something else.'})
    run = await dream.run_dream(uid, 'manual', llm=llm)
    assert run.status == dream.DONE
    assert len(await saved.list_memories(uid)) == 2
    assert [p.action for p in await dream.proposals_for(uid)] == ['delete']
    assert (await saved.get_profile(uid)).text == 'Written by me.'


async def test_conflict_needs_a_choice_and_stale_proposals_are_set_aside(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    a = await _raw(user, 'Lives in Accra.', created='2026-01-01 00:00:00')
    b = await _raw(user, 'Lives in Kumasi.', created='2026-01-02 00:00:00')
    llm = _LLM({'operations': [{'op': 'conflict', 'ids': [str(a.id), str(b.id)], 'reason': 'Two homes'},
                               {'op': 'delete', 'id': str(a.id)}], 'profile': ''})
    await dream.run_dream(uid, 'manual', llm=llm)
    conflict = next(p for p in await dream.proposals_for(uid) if p.action == 'conflict')
    with pytest.raises(dream.ProposalError):
        await dream.decide(conflict, True)
    await dream.decide(conflict, True, keep_id=str(b.id))
    assert [r.content for r in await saved.list_memories(uid)] == ['Lives in Kumasi.']

    stale = next(p for p in await dream.proposals_for(uid) if p.action == 'delete')
    with pytest.raises(dream.ProposalError):
        await dream.decide(stale, True)
    assert (await dream.DreamProposal.get(stale.id)).status == dream.PROPOSAL_STALE


async def test_night_dream_skips_when_nothing_changed(db, monkeypatch):  # noqa: F811
    from cognitrix.memory import dream

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await _raw(user, 'Likes tea.')
    first = await dream.run_dream(uid, 'night', llm=_LLM({'operations': [], 'profile': ''}))
    assert first.status == dream.DONE
    second = await dream.run_dream(uid, 'night', llm=_LLM({'operations': [], 'profile': ''}))
    assert second.status == dream.SKIPPED


async def test_bad_model_reply_fails_cleanly(db):  # noqa: F811
    from cognitrix.memory import dream

    user = await _user('Amos', 'amos@example.com')
    await _raw(user, 'Likes tea.')

    class Broken(_LLM):
        async def __call__(self, messages, stream=False):
            return SimpleNamespace(llm_response='not json at all', error=None)

    run = await dream.run_dream(str(user.id), 'manual', llm=Broken({}))
    assert run.status == dream.FAILED


def test_parse_reply_accepts_fenced_json():
    from cognitrix.memory import dream

    plan = dream.parse_reply('Here you go:\n```json\n{"operations": [{"op": "tag"}], "profile": " Hi "}\n```')
    assert plan == {'operations': [{'op': 'tag'}], 'profile': 'Hi'}


async def test_dream_routes(db, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import memories as routes
    from cognitrix.memory import dream

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    started = []

    async def fake_start(user_id, trigger='manual'):
        started.append((user_id, trigger))
        run = dream.DreamRun(user_id=user_id, trigger=trigger, status=dream.RUNNING, started=dream.now_str())
        await run.save()
        return await dream.DreamRun.find_one({'user_id': user_id})

    monkeypatch.setattr(dream, 'start', fake_start)
    result = await routes.dream_now(_ctx(amos))
    assert started == [(str(amos.id), 'manual')] and result['status'] == dream.RUNNING

    listed = await routes.dream_status(_ctx(amos))
    assert len(listed['runs']) == 1 and listed['proposals'] == [] and listed['running'] is True
    assert (await routes.dream_status(_ctx(bea)))['runs'] == []

    profile = await routes.put_profile(routes.ProfileBody(text='Amos, in Accra.'), _ctx(amos))
    assert profile['text'] == 'Amos, in Accra.' and profile['locked'] is True

    added = await routes.add_memory(routes.MemoryBody(content='Prefers tea.', kind='preference'), _ctx(amos))
    history = await routes.memory_history(ctx=_ctx(amos))
    assert history['changes'][0]['by'] == 'person'
    with pytest.raises(HTTPException):
        await routes.undo_change(history['changes'][0]['id'], _ctx(bea))
    await routes.undo_change(history['changes'][0]['id'], _ctx(amos))
    assert (await routes.list_memories(ctx=_ctx(amos)))['memories'] == []
    assert added['kind'] == 'preference'


async def test_undo_chain_keeps_the_memory_id(db):  # noqa: F811
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    row, _ = await saved.add(uid, 'Lives in Accra.', by='person')
    row = await saved.update(row, content='Lives in Kumasi.', by='person')
    await saved.delete(row, by='person')
    deleted, updated, created = await saved.history(uid)
    back = await saved.undo(deleted)
    assert str(back.id) == str(row.id) and back.content == 'Lives in Kumasi.'
    again = await saved.undo(updated)
    assert str(again.id) == str(row.id) and again.content == 'Lives in Accra.'
    assert len(await saved.list_memories(uid)) == 1
    await saved.undo(created)
    assert await saved.list_memories(uid) == []


async def test_non_latin_memories_are_not_duplicates(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    await _raw(user, '喜欢喝茶')
    await _raw(user, '住在北京')
    await _raw(user, '!!!')
    await _raw(user, '???')
    rows = await saved.list_memories(str(user.id))
    proposals = []
    counts = await dream.free_pass(str(user.id), rows, 'd1', auto=True, proposals=proposals)
    assert counts['duplicates'] == 0 and proposals == []
    assert dream._norm('喜欢 喝茶!') == '喜欢 喝茶'


async def test_agent_only_memories_stay_out_of_the_model_pass(db):  # noqa: F811
    from cognitrix.memory import dream

    user = await _user('Amos', 'amos@example.com')
    await _raw(user, 'Likes tea.')
    await _raw(user, 'Secret project codename is Falcon.', scope='agent', agent_id='a1')
    llm = _LLM({'operations': [], 'profile': 'Likes tea.'})
    await dream.run_dream(str(user.id), 'manual', llm=llm)
    sent = llm.prompts[0][-1]['content']
    assert 'Likes tea.' in sent and 'Falcon' not in sent


async def test_forgetting_a_memory_drops_the_dream_summary(db):  # noqa: F811
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    row, _ = await saved.add(uid, 'Lives in Accra.')
    await saved.set_profile(uid, 'Amos lives in Accra.', dream_id='d1')
    await saved.delete(row, by='person')
    assert await saved.get_profile(uid) is None
    # A summary the person locked is theirs and stays.
    row, _ = await saved.add(uid, 'Likes tea.')
    await saved.set_profile(uid, 'Written by me.', locked=True)
    await saved.update(row, content='Likes green tea.', by='agent')
    assert (await saved.get_profile(uid)).text == 'Written by me.'
