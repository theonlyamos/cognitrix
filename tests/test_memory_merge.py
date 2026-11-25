"""Memories about the same subject end up as one memory.

- The tidy groups memories on one subject (by meaning or shared topic words)
  and shows them to the model as hints, by short names ("m1") not full ids.
- A merge of agent-saved memories applies on its own only when the merged
  text keeps every name and number; otherwise it waits for the person.
- A memory another one already says word for word is dropped (free, no model).
- Saving: a fact an existing memory already says is not saved twice, and a
  fuller version of an agent's memory grows that memory instead of adding one.
- Agents see short ids, and short ids work everywhere a full id does.
"""
from tests.test_dream_mode import _LLM, _fake_embedder, _raw
from tests.test_saved_memories import _agent, _as, _run, _user, db  # noqa: F401

STACK = ('Wants a Monday stack briefing on breaking changes, security issues, new capabilities, and practical '
         'actions for Next.js, TypeScript, Node.js, Supabase, PostgreSQL, Vercel, Shopify apps, and major AI APIs.')
LAW = 'Wants a Monday briefing for Law of the Land on AI-powered legal research and access to authoritative legal information.'
INSYTE = 'Wants a Monday briefing for InsyteAI on AI tutoring and education technology.'
FRENCH_LEVEL = ('Is a beginner French learner who prefers 10-minute practical role-plays with useful phrases, '
                'gentle corrections, and a quick review.')
FRENCH_TIME = 'Prefers French practice on Mondays, Wednesdays, and Fridays at 8:00 a.m. Accra time.'
TEA = 'Prefers green tea in the afternoon.'


def test_keeps_details():
    from cognitrix.memory import dream

    merged = ('Is a beginner French learner: practice on Mondays, Wednesdays and Fridays at 8:00 a.m. Accra time, '
              'as 10-minute practical role-plays with useful phrases, gentle corrections and a quick review.')
    assert dream.keeps_details([FRENCH_LEVEL, FRENCH_TIME], merged)
    # A lost time, day or place is a lost detail.
    assert not dream.keeps_details([FRENCH_LEVEL, FRENCH_TIME], merged.replace('8:00 a.m. ', ''))
    assert not dream.keeps_details([FRENCH_LEVEL, FRENCH_TIME], merged.replace('Accra time', 'local time'))
    assert not dream.keeps_details([FRENCH_LEVEL, FRENCH_TIME], merged.replace('Wednesdays and ', ''))
    # A summary that drops most of what a memory said doesn't pass either.
    assert not dream.keeps_details([FRENCH_LEVEL, FRENCH_TIME],
                                   'Learns French on Mondays, Wednesdays and Fridays at 8:00 a.m. Accra time (10).')


async def test_related_memories_are_grouped_for_the_model(db):  # noqa: F811
    from cognitrix.memory import dream

    user = await _user('Amos', 'amos@example.com')
    rows = [await _raw(user, text) for text in (STACK, FRENCH_LEVEL, LAW, TEA, INSYTE, FRENCH_TIME)]
    # No embedder in tests: shared topic words alone find the subjects.
    pairs = await dream.related_pairs(rows)
    found = [[rows[i].content for i in g] for g in dream.groups(pairs)]
    assert [FRENCH_LEVEL, FRENCH_TIME] in found
    assert any(LAW in g and INSYTE in g for g in found)
    assert not any(TEA in g for g in found)

    prompt = dream.build_prompt(rows, pairs, '', '')[-1]['content']
    assert 'm2, m6' in prompt                       # the French pair, by short name
    assert str(rows[0].id) not in prompt            # no full ids


async def test_groups_respect_the_size_cap():
    from cognitrix.memory import dream

    pairs = [(i, i + 1, 0.9) for i in range(20)]
    assert all(len(g) <= 4 for g in dream.groups(pairs, size=4))


async def test_tidy_merges_memories_on_one_subject(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    rows = [await _raw(user, text, created=f'2026-11-2{i} 00:00:00', kind='preference')
            for i, text in enumerate((FRENCH_LEVEL, FRENCH_TIME, LAW, INSYTE, TEA))]
    french = ('Is a beginner French learner who practises on Mondays, Wednesdays and Fridays at 8:00 a.m. Accra '
              'time with 10-minute practical role-plays, useful phrases, gentle corrections and a quick review.')
    lossy = 'Wants Monday briefings on AI for legal research and education.'
    llm = _LLM({'operations': [
        # The model sees the newest first: m1 tea, m2 InsyteAI, m3 Law of the Land, m4-m5 French.
        {'op': 'merge', 'ids': ['m4', 'm5'], 'content': french, 'kind': 'preference'},
        {'op': 'merge', 'ids': ['m2', 'm3'], 'content': lossy},
    ], 'profile': ''})
    run = await dream.run_dream(uid, 'manual', llm=llm)
    assert run.status == dream.DONE, run.error
    stats = dream.stats_of(run)
    assert stats['merged'] == 1 and stats['merged_from'] == 2
    assert 'combined 2 related memories into 1' in run.summary.lower()

    contents = sorted(r.content for r in await saved.list_memories(uid))
    assert french in contents and FRENCH_LEVEL not in contents and FRENCH_TIME not in contents
    # The oldest memory keeps its id; the merge is in its history with undo.
    assert (await saved.get_memory(uid, str(rows[0].id))).content == french
    # Dropping "Law of the Land" and "InsyteAI" isn't safe: the person decides.
    assert LAW in contents and INSYTE in contents
    proposals = await dream.proposals_for(uid)
    assert [(p.action, p.content) for p in proposals] == [('merge', lossy)]
    assert sorted(dream.ids_of(proposals[0])) == sorted([str(rows[2].id), str(rows[3].id)])

    await dream.undo_dream(uid, str(run.id))
    contents = sorted(r.content for r in await saved.list_memories(uid))
    assert FRENCH_LEVEL in contents and FRENCH_TIME in contents


async def test_long_merges_fit(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    for text in (STACK, LAW, INSYTE):
        await _raw(user, text)
    merged = ('Wants Monday briefings: a stack briefing on breaking changes, security issues, new capabilities and '
              'practical actions for Next.js, TypeScript, Node.js, Supabase, PostgreSQL, Vercel, Shopify apps and '
              'major AI APIs; one for Law of the Land on AI-powered legal research and access to authoritative legal '
              'information; one for InsyteAI on AI tutoring and education technology.')
    assert saved.MAX_CHARS < len(merged) <= saved.STORED_MAX_CHARS
    llm = _LLM({'operations': [{'op': 'merge', 'ids': ['m1', 'm2', 'm3'], 'content': merged}], 'profile': ''})
    run = await dream.run_dream(uid, 'manual', llm=llm)
    assert dream.stats_of(run)['merged'] == 1, run.summary
    assert [r.content for r in await saved.list_memories(uid)] == [merged]


async def test_person_typed_memories_are_only_proposed(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await _raw(user, FRENCH_LEVEL, source='user')
    await _raw(user, FRENCH_TIME)
    llm = _LLM({'operations': [{'op': 'merge', 'ids': ['m1', 'm2'],
                                'content': FRENCH_LEVEL[:-1] + ', on Mondays, Wednesdays, and Fridays at 8:00 a.m. '
                                                               'Accra time.'}], 'profile': ''})
    await dream.run_dream(uid, 'manual', llm=llm)
    assert len(await saved.list_memories(uid)) == 2
    assert [p.action for p in await dream.proposals_for(uid)] == ['merge']


async def test_free_pass_drops_memories_another_one_already_says(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await _raw(user, 'Is learning French.')
    await _raw(user, 'Is learning French as a beginner, 10 minutes a day.')
    await _raw(user, 'Does not want calls on Fridays at all.')
    await _raw(user, 'Want calls on Fridays.')        # not said by the one above: it says the opposite
    run = await dream.run_dream(uid, 'manual', llm=_LLM({'operations': [], 'profile': ''}))
    assert dream.stats_of(run)['duplicates'] == 1
    contents = sorted(r.content for r in await saved.list_memories(uid))
    assert 'Is learning French.' not in contents and len(contents) == 3


async def test_covered_memory_survives_a_shorter_lived_one(db):  # noqa: F811
    from cognitrix.memory import dream, saved

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await _raw(user, 'Is learning French.')
    await _raw(user, 'Is learning French as a beginner on a short summer course.', valid_until='2020-08-31')
    await _raw(user, 'Plays the guitar.')
    await _raw(user, 'Plays the guitar in a band until Friday.', valid_until='2099-01-01')
    run = await dream.run_dream(uid, 'manual', llm=_LLM({'operations': [], 'profile': ''}))
    stats = dream.stats_of(run)
    assert stats['expired'] == 1 and stats['duplicates'] == 0
    contents = sorted(r.content for r in await saved.list_memories(uid))
    assert contents == ['Is learning French.', 'Plays the guitar in a band until Friday.', 'Plays the guitar.']

    # Saving: a dated memory doesn't stand in for a lasting fact.
    _row, created = await saved.add(uid, 'Plays the guitar in a band')
    assert created


async def test_saving_skips_what_is_already_said_and_grows_memories(db, monkeypatch):  # noqa: F811
    from cognitrix.memory import saved, semantic

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    full, created = await saved.add(uid, 'Is learning French as a beginner.')
    again, created_again = await saved.add(uid, 'is learning French')
    assert created and not created_again and str(again.id) == str(full.id)

    agent = _agent('helper', save_global=True)
    token = _as(user, session_id='chat-1')
    from cognitrix.tools.utils import reset_execution_context
    try:
        result = await _run(agent, action='save',
                            content='Is learning French as a beginner, with 10-minute role-plays.')
        assert result.content.startswith(f'Memory updated (id {saved.short_id(full)}, global)')
        rows = await saved.list_memories(uid)
        assert [r.content for r in rows] == ['Is learning French as a beginner, with 10-minute role-plays.']
        assert str(rows[0].id) == str(full.id)

        # Growing keeps an end date when the new save gives none.
        dated, _ = await saved.add(uid, 'Is on leave in Kumasi.', valid_until='2099-12-31')
        result = await _run(agent, action='save', content='Is on leave in Kumasi with family.')
        assert result.content.startswith('Memory updated')
        assert (await saved.get_memory(uid, str(dated.id))).valid_until == '2099-12-31'

        # A memory on the same subject is pointed out so the agent can fold it in.
        monkeypatch.setenv('COGNITRIX_MEMORY_EMBEDDINGS', '1')
        semantic.set_embedder(_fake_embedder([('french',)]))
        result = await _run(agent, action='save', content='Practises French on Mondays at 8:00 in Accra.')
        assert result.content.startswith('Memory saved (id ')
        assert 'same subject' in result.content and saved.short_id(full) in result.content
    finally:
        reset_execution_context(token)


async def test_short_ids(db):  # noqa: F811
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    other = await _user('Bea', 'bea@example.com')
    row, _ = await saved.add(str(user.id), 'Works on Cognitrix.')
    short = saved.short_id(row)
    assert len(short) == saved.SHORT_ID_LEN
    assert str((await saved.get_memory(str(user.id), short)).id) == str(row.id)
    assert await saved.get_memory(str(other.id), short) is None       # never someone else's
    assert await saved.get_memory(str(user.id), short[:3]) is None   # too short to trust

    block = await saved.prompt_block(str(user.id), _agent())
    assert f'(id {short})' in block and str(row.id) not in block


async def test_similar_pairs_by_matrix(monkeypatch):
    from cognitrix.memory import semantic

    monkeypatch.setenv('COGNITRIX_MEMORY_EMBEDDINGS', '1')
    semantic.set_embedder(_fake_embedder([('tea',), ('french',)]))
    pairs = await semantic.similar_pairs(['green tea', 'black tea', 'French lessons', 'tea and French'], 0.9)
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]
    scores = await semantic.scores('tea', ['iced tea', 'French'])
    assert scores[0] > 0.9 > scores[1]
