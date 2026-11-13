"""Artifacts: pages agents publish.

- Publish makes a page; publishing again adds a version at the same id.
- Private until shared; view/edit roles; a public link; "share this version".
- Comments, sent to the agent by people who can edit; rate limited.
- The frame is sandboxed and its token is not a sign-in token.
- Page powers need the page to declare them and the viewer to allow them.
- Documents an agent writes are listed with the pages.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'artifacts.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    monkeypatch.delenv('COGNITRIX_ARTIFACT_PUBLIC_LINKS', raising=False)
    yield tmp_path


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _as(user, **kw):
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    return set_execution_context(ToolExecutionContext(user_id=str(user.id), **kw))


AGENT = SimpleNamespace(id='a1', name='Researcher')


async def _tool(name, **params):
    from cognitrix.tools.base import ToolManager

    return (await ToolManager.get_by_name(name).run(**params, parent=AGENT)).outcome


def _page_id(outcome):
    return outcome.entities[0].id


# --- publishing and versions ---------------------------------------------------

async def test_publish_then_update_adds_a_version(db):
    from cognitrix.artifact_pages import store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    first = await _tool('publish_artifact', title='Shortlist', content='<h1>One</h1>')
    assert first.status == 'success' and first.entities[0].type == 'artifact_page'
    pid = _page_id(first)
    second = await _tool('publish_artifact', title='Shortlist', content='<h1>Two</h1>', artifact_id=pid, note='RAM column')
    assert 'version 2' in second.text

    page = await store.get_page(pid)
    assert page.version == 2 and page.session_id == 's1' and page.agent_id == 'a1'
    assert store.read_source(page, 1) == '<h1>One</h1>'
    assert store.read_source(page) == '<h1>Two</h1>'
    assert [v.number for v in await store.versions(page)] == [2, 1]


async def test_same_title_in_the_same_chat_updates_instead_of_duplicating(db):
    from cognitrix.artifact_pages import store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1')
    one = await _tool('publish_artifact', title='Board', content='a')
    two = await _tool('publish_artifact', title='board', content='b')
    assert _page_id(one) == _page_id(two)
    _as(amos, session_id='s2')
    three = await _tool('publish_artifact', title='Board', content='c')
    assert _page_id(three) != _page_id(one)
    assert len(await store.visible_pages(str(amos.id))) == 2


async def test_task_publish_marks_the_page_live(db):
    from cognitrix.artifact_pages import store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, task_id='t1', run_id='r1')
    out = await _tool('publish_artifact', title='Deploy health', content='ok')
    _as(amos, task_id='t1', run_id='r2')
    again = await _tool('publish_artifact', title='Deploy health', content='still ok')
    assert _page_id(out) == _page_id(again)
    page = await store.get_page(_page_id(out))
    people, agents = await store.names()
    assert store.project(page, 'owner', people=people, agents=agents)['live'] is True


async def test_bad_input_is_refused(db):
    amos = await _user('Amos', 'amos@example.com')
    _as(amos)
    assert (await _tool('publish_artifact', title='', content='x')).error.code == 'missing_title'
    assert (await _tool('publish_artifact', title='T', content='  ')).error.code == 'empty'
    assert (await _tool('publish_artifact', title='T', content='x', format='pdf')).error.code == 'bad_format'
    big = 'x' * (5 * 1024 * 1024 + 1)
    assert (await _tool('publish_artifact', title='T', content=big)).error.code == 'too_big'
    assert (await _tool('publish_artifact', title='T', content='x', artifact_id='nope')).error.code == 'not_found'


async def test_api_key_and_ownerless_turns_cannot_publish(db):
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    amos = await _user('Amos', 'amos@example.com')
    set_execution_context(ToolExecutionContext())
    assert (await _tool('publish_artifact', title='T', content='x')).error.code == 'no_owner'
    _as(amos, api_key_id='k1')
    assert (await _tool('publish_artifact', title='T', content='x')).error.code == 'no_owner'


async def test_restore_republishes_an_old_version(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))
    await _tool('publish_artifact', title='T', content='two', artifact_id=pid)
    out = await routes.restore_version(pid, routes.RestoreBody(version=1), _ctx(amos))
    assert out['version'] == 3
    assert (await routes.page_source(pid, None, _ctx(amos)))['source'] == 'one'


# --- sharing --------------------------------------------------------------------

async def test_private_until_shared_then_roles_apply(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    esi = await _user('Esi', 'esi@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))

    with pytest.raises(HTTPException) as err:
        await routes.get_page(pid, _ctx(kofi))
    assert err.value.status_code == 404
    assert (await routes.list_artifacts(_ctx(kofi)))['pages'] == []

    await routes.set_sharing(pid, routes.SharingUpdate(people=[
        routes.ShareEntry(user_id=str(kofi.id), role='view'),
        routes.ShareEntry(user_id=str(esi.id), role='edit'),
    ]), _ctx(amos))
    assert (await routes.get_page(pid, _ctx(kofi)))['role'] == 'view'
    assert (await routes.get_page(pid, _ctx(esi)))['role'] == 'edit'
    # Only the owner shares, pins or deletes.
    for call in (
        lambda: routes.get_sharing(pid, _ctx(esi)),
        lambda: routes.delete_page(pid, _ctx(esi)),
        lambda: routes.update_page(pid, routes.PageUpdate(pinned=True), _ctx(esi)),
        lambda: routes.restore_version(pid, routes.RestoreBody(version=1), _ctx(kofi)),
    ):
        with pytest.raises(HTTPException) as err:
            await call()
        assert err.value.status_code == 403

    # An editor's agent may update it; a viewer's may not.
    _as(esi)
    assert (await _tool('publish_artifact', title='T', content='two', artifact_id=pid)).status == 'success'
    _as(kofi)
    assert (await _tool('publish_artifact', title='T', content='three', artifact_id=pid)).error.code == 'not_found'
    assert (await _tool('read_artifact', artifact_id=pid)).status == 'success'


async def test_viewers_stay_on_the_shared_version(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))
    await routes.set_sharing(pid, routes.SharingUpdate(everyone='view', share_version=1), _ctx(amos))
    await _tool('publish_artifact', title='T', content='two', artifact_id=pid)

    assert (await routes.page_source(pid, None, _ctx(kofi)))['source'] == 'one'
    with pytest.raises(HTTPException):
        await routes.page_source(pid, 2, _ctx(kofi))
    assert (await routes.page_source(pid, None, _ctx(amos)))['source'] == 'two'
    seen = await routes.get_page(pid, _ctx(kofi))
    assert seen['viewer_version'] == 1 and [v['number'] for v in seen['versions']] == [1]

    # The viewer's own agent gets the shared version too, never a newer one.
    _as(kofi)
    read = await _tool('read_artifact', artifact_id=pid)
    assert read.status == 'success' and 'one' in read.text and 'two' not in read.text
    assert (await _tool('read_artifact', artifact_id=pid, version=2)).status != 'success'
    _as(amos)
    assert 'two' in (await _tool('read_artifact', artifact_id=pid)).text

    await routes.set_sharing(pid, routes.SharingUpdate(share_version=0), _ctx(amos))
    assert (await routes.page_source(pid, None, _ctx(kofi)))['source'] == 'two'


async def test_public_link_and_server_switch(db, monkeypatch):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='Notes', content='# Hi', format='markdown'))
    view = await routes.set_sharing(pid, routes.SharingUpdate(public=True), _ctx(amos))
    token = view['public_url'].rsplit('/', 1)[1]
    assert view['public'] and len(token) >= 24

    info = await routes.public_page(token)
    assert info['title'] == 'Notes' and info['owner'] == 'Amos'
    html = (await routes.public_frame(token, info['version'])).body.decode()
    assert 'marked' in html and '# Hi' in html
    with pytest.raises(HTTPException):
        await routes.public_frame(token, 99)

    # Turned off for the whole server: existing links stop, and come back with it.
    monkeypatch.setenv('COGNITRIX_ARTIFACT_PUBLIC_LINKS', 'off')
    with pytest.raises(HTTPException):
        await routes.public_page(token)
    with pytest.raises(HTTPException):
        await routes.set_sharing(pid, routes.SharingUpdate(public=True), _ctx(amos))
    monkeypatch.delenv('COGNITRIX_ARTIFACT_PUBLIC_LINKS')
    assert (await routes.public_page(token))['title'] == 'Notes'

    await routes.set_sharing(pid, routes.SharingUpdate(public=False), _ctx(amos))
    with pytest.raises(HTTPException):
        await routes.public_page(token)


# --- the frame ------------------------------------------------------------------

async def test_frame_is_sandboxed_and_its_token_is_not_a_login(db):
    from cognitrix.api.routes import artifact_pages as routes
    from cognitrix.artifact_pages import frame
    from cognitrix.common.security import create_access_token, verify_token

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T',
                               content='<!doctype html><html><head><title>x</title></head><body>hi</body></html>'))
    url = (await routes.frame_url(pid, None, _ctx(amos)))['url']
    token = url.rsplit('/', 1)[1]
    response = await routes.page_frame(token)
    csp = response.headers['content-security-policy']
    assert 'sandbox allow-scripts' in csp and 'allow-same-origin' not in csp
    assert "connect-src 'none'" in csp and "frame-ancestors 'self'" in csp
    body = response.body.decode()
    assert body.index('window.cognitrix') < body.index('<title>x</title>')

    assert await verify_token(token) is None
    assert frame.read_token(create_access_token({'sub': 'amos@example.com'})) is None
    assert frame.read_token('junk') is None
    # A token minted for someone who lost access stops working.
    stale = frame.frame_token(pid, 1, str(kofi.id))
    with pytest.raises(HTTPException):
        await routes.page_frame(stale)


def test_fragments_and_markdown_get_a_document():
    from cognitrix.artifact_pages import frame

    doc = frame.render('<p>hello</p>', 'html', 'A <b>title</b>')
    assert doc.startswith('<!doctype html>') and '<p>hello</p>' in doc and 'A &lt;b&gt;title' in doc
    md = frame.render('</script><script>alert(1)</script>', 'markdown', 'T')
    assert '</script><script>alert(1)' not in md


# --- comments -------------------------------------------------------------------

async def test_comments_threads_resolve_and_alerts(db, monkeypatch):
    from cognitrix.api.routes import artifact_pages as routes
    from cognitrix.artifact_pages import comments
    from cognitrix.tasks.automation import AgentNotification

    sent = []

    async def fake_send(page, comment):
        sent.append(comment.body)
        return 'sent'

    monkeypatch.setattr(comments, 'send_to_agent', fake_send)
    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))
    await routes.set_sharing(pid, routes.SharingUpdate(everyone='view'), _ctx(amos))

    # A viewer comments; asking for the agent does nothing for them.
    out = await routes.add_comment(pid, routes.CommentBody(
        body='Looks high', anchor={'x': 0.5, 'y': 2, 'text': '780 MB', 'junk': 1}, send_to_agent=True), _ctx(kofi))
    thread = out['threads'][0]
    assert thread['anchor'] == {'x': 0.5, 'y': 1.0, 'text': '780 MB'} and not thread['sent_to_agent']
    assert sent == [] and 'Only people who can edit' in out['agent']
    alerts = await AgentNotification.find({'user_id': str(amos.id)})
    assert len(alerts) == 1 and 'Kofi commented on T' in alerts[0].title and f'/artifacts/{pid}' in alerts[0].link

    # The owner replies with @agent: that goes to the agent.
    out = await routes.add_comment(pid, routes.CommentBody(body='@agent rerun it', thread_id=thread['id']), _ctx(amos))
    assert sent == ['@agent rerun it'] and out['threads'][0]['replies'][0]['sent_to_agent']

    # The agent answers and resolves with its tool.
    reply = await _tool('artifact_comments', artifact_id=pid, action='reply', thread_id=thread['id'], text='Fixed in v2')
    assert reply.status == 'success'
    await _tool('artifact_comments', artifact_id=pid, action='resolve', thread_id=thread['id'])
    listing = (await routes.list_comments(pid, _ctx(kofi)))['threads'][0]
    assert listing['resolved'] and listing['replies'][-1]['is_agent'] and listing['replies'][-1]['author_name'] == 'Researcher'
    assert any('Researcher commented' in a.title for a in await AgentNotification.find({'user_id': str(kofi.id)}))
    assert 'thread' in (await _tool('artifact_comments', artifact_id=pid)).text

    # A new reply reopens it.
    await routes.add_comment(pid, routes.CommentBody(body='One more thing', thread_id=thread['id']), _ctx(kofi))
    assert not (await routes.list_comments(pid, _ctx(kofi)))['threads'][0]['resolved']


async def test_sending_to_the_agent_is_rate_limited(db, monkeypatch):
    from cognitrix.artifact_pages import comments, store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, agent_id='a1')
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))
    page = await store.get_page(pid)
    delivered = []

    async def fake_deliver(page, text):
        delivered.append(text)

    monkeypatch.setattr(comments, '_deliver', fake_deliver)
    monkeypatch.setattr(comments, 'MAX_SENT_PER_HOUR', 2)
    comments._sent.clear()
    row = await comments.add(page, author_id=str(amos.id), author_name='Amos', body='please fix')
    assert await comments.send_to_agent(page, row) == 'sent'
    assert await comments.send_to_agent(page, row) == 'sent'
    assert 'Not sent' in await comments.send_to_agent(page, row)
    import asyncio

    await asyncio.sleep(0)
    assert len(delivered) == 2 and f'artifact_id {pid}' in delivered[0] and 'please fix' in delivered[0]


def test_mentions():
    from cognitrix.artifact_pages.comments import mentions_agent

    assert mentions_agent('@agent fix this')
    assert mentions_agent('hey @Researcher can you', 'Researcher')
    assert not mentions_agent('mail me at a@agent.com')
    assert not mentions_agent('no mention', 'Researcher')


# --- page powers ----------------------------------------------------------------

async def test_powers_need_declaring_and_allowing(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='one', powers=['data', 'storage', 'bogus']))
    await routes.set_sharing(pid, routes.SharingUpdate(everyone='view'), _ctx(amos))
    assert (await routes.get_page(pid, _ctx(amos)))['powers'] == ['data']

    call = routes.BridgeCall(kind='data.set', payload={'key': 'k', 'value': '"v"', 'shared': True})
    with pytest.raises(HTTPException) as err:
        await routes.bridge(pid, call, _ctx(amos))
    assert err.value.status_code == 403 and 'not allowed' in err.value.detail
    with pytest.raises(HTTPException) as err:
        await routes.bridge(pid, routes.BridgeCall(kind='ask', payload={'prompt': 'hi'}), _ctx(amos))
    assert 'did not ask' in err.value.detail

    assert (await routes.set_consent(pid, routes.ConsentBody(allowed=['data', 'ask']), _ctx(amos)))['allowed'] == ['data']
    await routes.set_consent(pid, routes.ConsentBody(allowed=['data']), _ctx(kofi))
    await routes.bridge(pid, call, _ctx(amos))
    await routes.bridge(pid, routes.BridgeCall(kind='data.set', payload={'key': 'mine', 'value': '1'}), _ctx(amos))

    get = lambda key, shared, who: routes.bridge(  # noqa: E731
        pid, routes.BridgeCall(kind='data.get', payload={'key': key, 'shared': shared}), _ctx(who))
    assert (await get('k', True, kofi))['value'] == '"v"'
    assert (await get('mine', False, kofi))['value'] is None
    assert (await get('mine', False, amos))['value'] == '1'
    await routes.bridge(pid, routes.BridgeCall(kind='data.remove', payload={'key': 'k', 'shared': True}), _ctx(kofi))
    assert (await routes.bridge(pid, routes.BridgeCall(kind='data.list', payload={'shared': True}), _ctx(amos)))['value'] == {}

    # A new version that drops the power takes it away.
    await _tool('publish_artifact', title='T', content='two', artifact_id=pid, powers=[])
    with pytest.raises(HTTPException):
        await get('mine', False, amos)


async def test_data_limits(db, monkeypatch):
    from cognitrix.artifact_pages import store

    amos = await _user('Amos', 'amos@example.com')
    page, _ = await store.publish(user_key=str(amos.id), title='T', content='x', powers=['data'])
    monkeypatch.setattr(store, 'MAX_DATA_KEYS', 2)
    await store.data_set(page, str(amos.id), 'shared', 'a', '1')
    await store.data_set(page, str(amos.id), 'shared', 'b', '1')
    with pytest.raises(store.PageError):
        await store.data_set(page, str(amos.id), 'shared', 'c', '1')
    with pytest.raises(store.PageError):
        await store.data_set(page, str(amos.id), 'shared', 'a', 'x' * (store.MAX_DATA_VALUE + 1))


# --- files and images in the list ----------------------------------------------

async def test_written_documents_are_listed_but_code_is_not(db):
    from cognitrix.api.routes import artifact_pages as routes
    from cognitrix.artifact_pages import store

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    report = db / 'report.md'
    report.write_text('# Report')
    script = db / 'run.py'
    script.write_text('print(1)')
    assert store.written_path('Write', f'Written to file: {report}\nSize: 8 bytes') == str(report)
    assert store.written_path('Write', 'Error: nope') is None
    assert store.written_path('Read', f'Written to file: {report}') is None
    for path in (report, script, report):
        await store.record_written_file(owner_id=str(amos.id), path=str(path), agent_id='a1', session_id='s1')

    files = (await routes.list_artifacts(_ctx(amos)))['files']
    assert [f['title'] for f in files] == ['report.md'] and files[0]['mime_type'] == 'text/markdown'
    # A chat's side panel asks for just what that chat made.
    assert [f['title'] for f in (await routes.list_artifacts(_ctx(amos), session_id='s1'))['files']] == ['report.md']
    assert (await routes.list_artifacts(_ctx(amos), session_id='s2'))['files'] == []
    assert (await routes.list_artifacts(_ctx(kofi)))['files'] == []
    with pytest.raises(HTTPException):
        await routes.get_file(files[0]['id'], False, _ctx(kofi))
    assert str((await routes.get_file(files[0]['id'], False, _ctx(amos))).path) == str(report)

    made = await routes.publish_file(files[0]['id'], None, _ctx(amos))
    assert (await routes.page_source(made['id'], None, _ctx(amos)))['source'] == '# Report'
    await routes.forget_file(files[0]['id'], _ctx(amos))
    assert report.exists() and (await routes.list_artifacts(_ctx(amos)))['files'] == []


async def test_delete_removes_everything(db):
    from cognitrix.api.routes import artifact_pages as routes
    from cognitrix.artifact_pages import store
    from cognitrix.artifact_pages.models import ArtifactComment, ArtifactVersion

    amos = await _user('Amos', 'amos@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))
    await routes.add_comment(pid, routes.CommentBody(body='hello'), _ctx(amos))
    folder = store.page_dir(pid)
    assert folder.is_dir()
    await routes.delete_page(pid, _ctx(amos))
    assert not folder.exists()
    assert not await ArtifactVersion.find({'page_id': pid}) and not await ArtifactComment.find({'page_id': pid})


async def test_pin_shows_on_the_dashboard_list(db):
    from cognitrix.api.routes import artifact_pages as routes

    amos = await _user('Amos', 'amos@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='one'))
    assert (await routes.pinned(_ctx(amos)))['pages'] == []
    await routes.update_page(pid, routes.PageUpdate(pinned=True, title='  New   name '), _ctx(amos))
    pinned = (await routes.pinned(_ctx(amos)))['pages']
    assert pinned[0]['title'] == 'New name' and pinned[0]['frame_url'].startswith('/api/v1/artifact-frame/')


def test_artifact_tools_do_not_ask_for_approval():
    """Publishing makes a page only its owner can see, so it never needs a yes."""
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel

    for name in ('Publish Artifact', 'Read Artifact', 'Artifact Comments'):
        assert DestructiveOpDetector().analyze(name, {}).risk_level == RiskLevel.LOW


async def test_pruning_keeps_the_version_viewers_are_held_on(db, monkeypatch):
    from cognitrix.api.routes import artifact_pages as routes
    from cognitrix.artifact_pages import store

    monkeypatch.setattr(store, 'MAX_VERSIONS_KEPT', 2)
    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _as(amos)
    pid = _page_id(await _tool('publish_artifact', title='T', content='v1'))
    await routes.set_sharing(pid, routes.SharingUpdate(everyone='view', share_version=1), _ctx(amos))
    for n in range(2, 7):
        await _tool('publish_artifact', title='T', content=f'v{n}', artifact_id=pid)

    assert (await routes.page_source(pid, None, _ctx(kofi)))['source'] == 'v1'
    page = await store.get_page(pid)
    with pytest.raises(store.PageError):
        store.read_source(page, 2)  # an old version nobody is held on is gone


def test_pages_may_only_call_app_tools_that_change_nothing():
    """The rule the apps power uses: an undoable change is still a change."""
    from cognitrix.safety.destructive_ops import mcp_only_reads

    assert mcp_only_reads('list_issues', None) and mcp_only_reads('anything', {'readOnlyHint': True})
    for name in ('create_issue', 'update_record', 'add_label', 'send_message'):
        assert not mcp_only_reads(name, None)


async def test_app_errors_reach_the_page_as_a_plain_failure(db, monkeypatch):
    """What an app said when it failed stays in the log, not in the page."""
    from cognitrix.api.routes import artifact_pages as routes
    from cognitrix.artifact_pages import store
    from cognitrix.mcp import apps, client, registry

    config = SimpleNamespace(name='Tracker', key='tracker')
    results = iter(['Error: token abc123 rejected by upstream', '{"items": [1, 2]}', 'plain text'])

    async def my_apps(me):
        return [config]

    async def call_tool(*_a, **_k):
        return next(results)

    monkeypatch.setattr(routes, '_my_apps', my_apps)
    monkeypatch.setattr(client, 'get_dynamic_client', lambda: _none())
    monkeypatch.setattr(registry, 'prepare', lambda *_a: _none())
    monkeypatch.setattr(registry, 'matches', lambda c, app: True)
    monkeypatch.setattr(apps, 'list_tools', lambda *_a: _value([{'name': 'list_issues'}]))
    monkeypatch.setattr(apps, 'call_tool', call_tool)

    with pytest.raises(store.PageError) as failed:
        await routes._apps_call('u1', 'Tracker', 'list_issues', {})
    assert 'abc123' not in failed.value.message and failed.value.status == 502
    assert await routes._apps_call('u1', 'Tracker', 'list_issues', {}) == {'items': [1, 2]}
    assert await routes._apps_call('u1', 'Tracker', 'list_issues', {}) == 'plain text'


async def _none():
    return None


async def _value(value):
    return value
