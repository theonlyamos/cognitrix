"""Skills backup to Notion, next to the memory backup.

- A chat review that writes a skill backs the skills up to their own page;
  unchanged skills aren't written again; Back up now always writes.
- Bodies with code fences survive the round trip; secret-looking skills stay out.
- Restore on a fresh install adds the missing skills and keeps the ones already here.
- A fresh install doesn't overwrite a backup holding skills it doesn't have.
- The routes back up and restore memories and skills together.
"""
import json
from types import SimpleNamespace

import pytest

from tests.test_chat_review import _LLM, LONG_JOB, SKILL_BODY, _chat, _later, _reply, notes  # noqa: F401
from tests.test_saved_memories import _ctx, _user, db  # noqa: F401

FENCED = ('Steps:\n1. Run the export.\n```bash\npython export.py --all\n```\n'
          '2. Check the totals match the dashboard before sending.')


class _FakeNotion:
    """Notion MCP tools, keeping each page's title."""

    def __init__(self):
        self.pages: dict[str, tuple[str, str]] = {}
        self.calls: list[tuple[str, str]] = []

    async def call(self, base, args):
        from cognitrix.memory import notion_backup as nb

        self.calls.append((base, args.get('query') or args.get('id') or args.get('page_id') or ''))
        if base == 'search':
            return json.dumps({'results': [
                {'id': pid, 'title': title, 'url': f'https://www.notion.so/{pid}'}
                for pid, (title, _) in self.pages.items() if title == args['query']]})
        if base == 'fetch':
            if args['id'] not in self.pages:
                raise nb.BackupError('missing')
            title, content = self.pages[args['id']]
            return json.dumps({'title': title, 'text': f'<page><content>{content}</content></page>'})
        if base == 'create-pages':
            pid = f'{len(self.pages) + 1:032x}'
            page = args['pages'][0]
            self.pages[pid] = (page['properties']['title'], page['content'])
            return json.dumps({'pages': [{'id': pid, 'url': f'https://www.notion.so/{pid}'}]})
        if base == 'update-page':
            if args['page_id'] not in self.pages:
                raise nb.BackupError('gone')
            self.pages[args['page_id']] = (self.pages[args['page_id']][0], args['new_str'])
            return '{}'
        raise AssertionError(base)

    def page(self, title):
        return next(content for t, content in self.pages.values() if t == title)

    def writes(self):
        return [c for c in self.calls if c[0] in ('create-pages', 'update-page')]


@pytest.fixture
def notion(monkeypatch):
    from cognitrix.memory import notion_backup as nb

    fake = _FakeNotion()

    async def _open(user_id):
        return fake

    async def find_connection(user_id):
        return SimpleNamespace(name='Notion', catalog_id='notion')

    monkeypatch.setattr(nb, '_open', _open)
    monkeypatch.setattr(nb, 'find_connection', find_connection)
    return fake


async def test_a_review_that_writes_a_skill_backs_skills_up(db, notes, notion):  # noqa: F811
    from cognitrix.memory import review
    from cognitrix.skills import notion_backup as sb
    from cognitrix.skills import personal

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await _chat(user, LONG_JOB)
    skill = {'update': '', 'name': 'monthly-invoice-totals', 'description': 'Total invoices by client.',
             'body': SKILL_BODY}
    await review.run_review(uid, 'manual', llm=_LLM(_reply(skill=skill)), now=_later())
    assert [s.name for s in await personal.skills_of(uid)], 'the review wrote a skill'
    state = await sb.get_state(uid)
    assert state.status == sb.OK and state.count == 1 and state.owned
    assert sb.PAGE_TITLE in [t for t, _ in notion.pages.values()]

    # Nothing changed: the tidy's hook doesn't write the page again.
    before = len(notion.writes())
    await sb.after_change(uid, 'tidy')
    assert len(notion.writes()) == before
    # Back up now always writes.
    await sb.backup(uid, trigger='manual')
    assert len(notion.writes()) == before + 1


async def test_round_trip_keeps_fences_and_leaves_secrets_out(db, notion):  # noqa: F811
    from cognitrix.skills import notion_backup as sb
    from cognitrix.skills import personal

    old = await _user('Amos', 'amos@example.com')
    await personal.create(str(old.id), 'monthly-export', 'Use when exporting the monthly report.', FENCED,
                          author='Chat review')
    row = await personal.create(str(old.id), 'deploy-site', 'Use when deploying the site.', 'Deploy steps ' * 5)
    await personal.PersonalSkill.update_one({'id': row.id}, {'body': 'Use token sk-abcdefghijklmnopqrstuvwxyz123456 to deploy.'})
    state = await sb.backup(str(old.id), trigger='manual')
    assert state.status == sb.OK and state.count == 1 and 'Left out 1' in state.message
    page = notion.page(sb.PAGE_TITLE)
    assert 'sk-abc' not in page and '**monthly-export** (Chat review)' in page

    new = await _user('Amos', 'amos@example.org')
    nid = str(new.id)
    await personal.create(nid, 'monthly-export', 'Use when exporting the monthly report.', 'My own newer steps. ' * 3)
    result = await sb.restore(nid)
    assert result['restored'] == 0 and result['kept'] == 1, "this install's own copy wins"

    third = await _user('Amos', 'amos@example.net')
    result = await sb.restore(str(third.id))
    assert result['restored'] == 1
    restored = await personal.get(str(third.id), 'monthly-export')
    assert restored.body == FENCED and restored.author == 'Chat review'
    assert (await sb.restore(str(third.id)))['already'] == 1


async def test_fresh_install_does_not_wipe_skills_it_could_restore(db, notion):  # noqa: F811
    from cognitrix.skills import notion_backup as sb
    from cognitrix.skills import personal

    old = await _user('Amos', 'amos@example.com')
    await personal.create(str(old.id), 'monthly-export', 'Use when exporting the monthly report.', FENCED)
    await sb.backup(str(old.id), trigger='manual')

    new = await _user('Amos', 'amos@example.org')
    nid = str(new.id)
    assert await sb.offer_restore(nid) is True
    await personal.create(nid, 'write-post', 'Use when writing a blog post.', 'Outline first, then draft. ' * 3)
    state = await sb.backup(nid, trigger='review')
    assert state.status == sb.RESTORE_FIRST
    assert 'monthly-export' in notion.page(sb.PAGE_TITLE)

    await sb.restore(nid)
    state = await sb.backup(nid, trigger='review')
    assert state.status == sb.OK and len(notion.pages) == 1
    page = notion.page(sb.PAGE_TITLE)
    assert 'monthly-export' in page and 'write-post' in page


async def test_routes_do_memories_and_skills_together(db, notion):  # noqa: F811
    from cognitrix.api.routes import memories as routes
    from cognitrix.memory import saved
    from cognitrix.skills import personal

    user = await _user('Amos', 'amos@example.com')
    ctx = _ctx(user)
    await saved.add(str(user.id), 'Prefers short answers.', source='user')
    await personal.create(str(user.id), 'monthly-export', 'Use when exporting the monthly report.', FENCED)
    status = await routes.notion_backup_now(ctx=ctx)
    assert status['status'] == 'ok' and status['skills']['status'] == 'ok' and status['skills']['count'] == 1
    assert len(notion.pages) == 2
    result = await routes.notion_restore(ctx=ctx)
    assert result['already'] == 1 and result['skills']['already'] == 1

    # Only a skills page (no memory backup): restore still brings the skills back.
    other = await _user('Ama', 'ama@example.com')
    notion.pages = {pid: page for pid, page in notion.pages.items() if page[0] != 'Cognitrix memory backup'}
    result = await routes.notion_restore(ctx=_ctx(other))
    assert result['restored'] == 0 and result['skills']['restored'] == 1
