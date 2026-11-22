"""Memory backup to Notion after every tidy, and restoring it on a fresh install.

- After a finished tidy the memories go to one Notion page; later tidies
  replace that page instead of making new ones.
- Secret-looking notes and pending proposals are never written out.
- Without Notion the tidy still finishes and the status says Notion isn't connected.
- Restore finds the page by its title on a fresh install, brings memories back
  approved, maps agent-only notes by agent name, and holds the rest for approval.
- A fresh install with a few notes doesn't overwrite a bigger backup on its
  nightly tidy; Back up now does.
"""
import json
from types import SimpleNamespace

import pytest

from tests.test_dream_mode import _LLM, _raw
from tests.test_saved_memories import _ctx, _user, db  # noqa: F401


class _FakeNotion:
    """Stands in for the Notion MCP tools a backup uses."""

    def __init__(self):
        self.pages: dict[str, str] = {}
        self.calls: list[str] = []

    async def call(self, base, args):
        from cognitrix.memory import notion_backup as nb

        self.calls.append(base)
        if base == 'search':
            return json.dumps({'results': [
                {'id': pid, 'title': nb.PAGE_TITLE, 'url': f'https://www.notion.so/{pid}'} for pid in self.pages]})
        if base == 'fetch':
            if args['id'] not in self.pages:
                raise nb.BackupError('missing')
            return json.dumps({'title': nb.PAGE_TITLE, 'text': f'<page><content>{self.pages[args["id"]]}</content></page>'})
        if base == 'create-pages':
            pid = f'{len(self.pages) + 1:032x}'
            page = args['pages'][0]
            assert page['properties']['title'] == nb.PAGE_TITLE
            self.pages[pid] = page['content']
            return json.dumps({'pages': [{'id': pid, 'url': f'https://www.notion.so/{pid}'}]})
        if base == 'update-page':
            if args['page_id'] not in self.pages:
                raise nb.BackupError('gone')
            assert args['command'] == 'replace_content'
            self.pages[args['page_id']] = args['new_str']
            return json.dumps({'page_id': args['page_id']})
        raise AssertionError(base)


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


async def test_backup_after_tidy_updates_one_page(db, notion):  # noqa: F811
    from cognitrix.memory import dream, saved
    from cognitrix.memory import notion_backup as nb

    user = await _user('Amos', 'amos@example.com')
    uid = str(user.id)
    await saved.add(uid, 'Prefers short answers.', source='user')
    # Saving refuses secrets; an older row could still hold one.
    await _raw(user, 'My api key is sk-abcdefghijklmnopqrstuvwxyz123456', source='user')
    await saved.add(uid, 'Maybe likes tea.', pending=True)

    run = await dream.run_dream(uid, 'manual', llm=_LLM({'operations': [], 'profile': 'Amos builds Cognitrix.'}))
    assert run.status == dream.DONE
    assert len(notion.pages) == 1
    content = next(iter(notion.pages.values()))
    assert 'Prefers short answers.' in content and 'Amos builds Cognitrix.' in content
    assert 'sk-abc' not in content and 'likes tea' not in content
    state = await nb.get_state(uid)
    assert state.status == nb.OK and state.count == 1 and state.page_url.startswith('https://www.notion.so/')

    await saved.add(uid, 'Lives in Accra.', source='user')
    await dream.run_dream(uid, 'manual', llm=_LLM({'operations': [], 'profile': ''}))
    assert len(notion.pages) == 1, 'the same page is replaced, not duplicated'
    assert 'Lives in Accra.' in next(iter(notion.pages.values()))
    assert notion.calls.count('create-pages') == 1


async def test_tidy_without_notion_is_quiet(db):  # noqa: F811
    from cognitrix.memory import dream, saved
    from cognitrix.memory import notion_backup as nb

    user = await _user('Ama', 'ama@example.com')
    uid = str(user.id)
    await saved.add(uid, 'Prefers tea.', source='user')
    run = await dream.run_dream(uid, 'manual', llm=_LLM({'operations': [], 'profile': ''}))
    assert run.status == dream.DONE
    state = await nb.get_state(uid)
    assert state.status == nb.NOT_CONNECTED and 'Connected apps' in state.message


async def test_backups_off_skip_the_tidy_hook(db, notion):  # noqa: F811
    from cognitrix.memory import dream, saved
    from cognitrix.memory import notion_backup as nb

    user = await _user('Kofi', 'kofi@example.com')
    uid = str(user.id)
    await nb.set_enabled(uid, False)
    await saved.add(uid, 'Prefers tea.', source='user')
    await dream.run_dream(uid, 'manual', llm=_LLM({'operations': [], 'profile': ''}))
    assert notion.pages == {}


async def test_restore_on_a_fresh_install(db, notion, monkeypatch):  # noqa: F811
    from cognitrix.memory import notion_backup as nb
    from cognitrix.memory import saved

    old = await _user('Amos', 'amos@example.com')
    agents = {'a1': 'Coder'}

    async def names(user_id):
        return dict(agents), {}

    monkeypatch.setattr(nb, '_names', names)
    await saved.add(str(old.id), 'Prefers short answers.', source='user', pinned=True)
    await saved.add(str(old.id), 'Runs tests with pytest.', scope='agent', agent_id='a1', source='user')
    await saved.add(str(old.id), 'Uses the old Writer agent for posts.', scope='agent', agent_id='a2', source='user')
    await saved.set_profile(str(old.id), 'Amos builds Cognitrix.')
    state = await nb.backup(str(old.id), trigger='manual')
    assert state.status == nb.OK and state.count == 3

    # Reinstall: a new database, the same person, an agent with the same name but a new id.
    new = await _user('Amos again', 'amos2@example.com')
    new_id = str(new.id)
    agents.clear()
    agents['b7'] = 'Coder'
    result = await nb.restore(new_id)
    assert result['restored'] == 2 and result['waiting'] == 1 and result['about_you'] is True
    rows = {r.content: r for r in await saved.list_memories(new_id)}
    assert rows['Prefers short answers.'].pinned and not rows['Prefers short answers.'].pending
    assert rows['Runs tests with pytest.'].scope == 'agent' and rows['Runs tests with pytest.'].agent_id == 'b7'
    writer = rows['Uses the old Writer agent for posts.']
    assert writer.pending and writer.scope == 'global'
    assert (await saved.get_profile(new_id)).text == 'Amos builds Cognitrix.'

    again = await nb.restore(new_id)
    assert again['restored'] == 0 and again['already'] == 3


async def test_fresh_tidy_does_not_wipe_a_bigger_backup(db, notion):  # noqa: F811
    from cognitrix.memory import notion_backup as nb
    from cognitrix.memory import saved

    old = await _user('Amos', 'amos@example.com')
    for text in ('Prefers short answers.', 'Lives in Accra.', 'Builds Cognitrix.'):
        await saved.add(str(old.id), text, source='user')
    await nb.backup(str(old.id), trigger='manual')
    page = next(iter(notion.pages))

    new = await _user('Amos', 'amos@example.org')
    await saved.add(str(new.id), 'Likes tea.', source='user')
    state = await nb.backup(str(new.id), trigger='tidy')
    assert state.status == nb.RESTORE_FIRST
    assert 'Builds Cognitrix.' in notion.pages[page]

    state = await nb.backup(str(new.id), trigger='manual')
    assert state.status == nb.OK and len(notion.pages) == 1
    assert 'Likes tea.' in notion.pages[page] and 'Builds Cognitrix.' not in notion.pages[page]


def test_parse_reads_the_list_when_the_data_block_is_gone():
    from cognitrix.memory import notion_backup as nb

    page = '\n'.join(['Intro.', '', '## About you', '', 'Amos builds Cognitrix.', '', '## All agents', '',
                      '- Prefers short answers. (pinned)', '- Lives in Accra.', '', '## Only Coder', '', '- Uses pytest.'])
    data = nb.parse(page)
    assert data['about_you'] == 'Amos builds Cognitrix.'
    assert data['memories'][0] == {'content': 'Prefers short answers.', 'scope': 'global', 'pinned': True}
    assert data['memories'][2]['scope'] == 'other'
    assert nb.parse('Just a page.') is None


async def test_routes(db, notion):  # noqa: F811
    from cognitrix.api.routes import memories as routes
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    ctx = _ctx(user)
    await saved.add(str(user.id), 'Prefers short answers.', source='user')
    status = await routes.notion_status(ctx=ctx)
    assert status['connected'] is True and status['status'] == ''
    status = await routes.notion_backup_now(ctx=ctx)
    assert status['status'] == 'ok' and status['count'] == 1 and status['page_url']
    status = await routes.notion_settings(routes.NotionBackupBody(enabled=False), ctx=ctx)
    assert status['enabled'] is False
    result = await routes.notion_restore(ctx=ctx)
    assert result['already'] == 1


async def test_connecting_notion_on_a_fresh_install_offers_the_backup(db, notion):  # noqa: F811
    from cognitrix.memory import notion_backup as nb
    from cognitrix.memory import saved
    from cognitrix.tasks.automation import AgentNotification

    old = await _user('Amos', 'amos@example.com')
    await saved.add(str(old.id), 'Prefers short answers.', source='user')
    await nb.backup(str(old.id), trigger='manual')

    new = await _user('Amos', 'amos@example.net')
    assert await nb.offer_restore(str(new.id)) is True
    notes = list(await AgentNotification.find({'user_id': str(new.id)}))
    assert notes and notes[0].link == '/settings/memory'
    assert (await nb.get_state(str(new.id))).status == nb.RESTORE_FIRST

    # Someone who already has memories isn't asked.
    assert await nb.offer_restore(str(old.id)) is False
