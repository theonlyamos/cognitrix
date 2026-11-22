"""Files agents make: Share File, the end-of-turn scan, and the signed links.

- Kinds and noise filters decide what counts as a result.
- The sandbox's find output is parsed; local folders are walked.
- Share File copies a file in and returns a card ref on the tool outcome.
- The end of a turn finds new files; with nothing shared, likely results
  become cards and the rest go on the shelf.
- Content links are signed per file and kind of link, and only image, video,
  audio and PDF open in place.
"""
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'files.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setattr(settings, 'workdir', tmp_path / 'app')
    yield work


def _as(user_id, work, **kw):
    from cognitrix.common import folders as F
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    folder = F.Folder(str(work.resolve()), F.WRITE, 'work', True)
    return set_execution_context(ToolExecutionContext(user_id=user_id, folders=(folder,), **kw))


async def share_file(paths, caption=''):
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name('Share File')
    return (await tool.run(paths=paths, caption=caption)).outcome


def _png(path: Path, size=(64, 48)) -> Path:
    from PIL import Image

    Image.new('RGB', size, (200, 40, 40)).save(path)
    # Big enough to count as a result, not an icon.
    with open(path, 'ab') as fh:
        fh.write(b'\0' * 5000)
    return path


# ------------------------------------------------------------------ kinds

def test_kinds_and_noise():
    from cognitrix.produced_files import kinds

    assert kinds.kind_of('clip.MP4')[1] == 'video'
    assert kinds.kind_of('report.pdf') == ('application/pdf', 'pdf')
    assert kinds.kind_of('data.xlsx')[1] == 'sheet'
    assert kinds.kind_of('notes.md')[1] == 'text'
    assert kinds.kind_of('main.py')[1] == 'code'
    assert kinds.kind_of('mystery.zzz')[1] == 'other'

    assert kinds.noise('.hidden.png', 100)
    assert kinds.noise('package-lock.json', 100)
    assert kinds.noise('empty.png', 0)
    assert kinds.noise('script.py', 100)
    assert not kinds.noise('chart.png', 100)

    assert kinds.looks_like_result('chart.png', 50_000)
    assert not kinds.looks_like_result('icon.png', 1_000)
    assert not kinds.looks_like_result('data.json', 50_000)


def test_parse_find_and_walk(tmp_path):
    from cognitrix.produced_files import sources

    since = time.time() - 10
    text = f'{since + 5}\t120\t/home/agent/out/a.png\n{since - 100}\t5\t/home/agent/old.txt\nnot a line\n'
    found = sources.parse_find(text, since)
    assert [f.path for f in found] == ['/home/agent/out/a.png']
    assert found[0].size == 120

    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules' / 'x.js').write_text('x')
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out' / 'video.mp4').write_bytes(b'v' * 10)
    old = tmp_path / 'old.txt'
    old.write_text('old')
    os.utime(old, (since - 100, since - 100))
    names = {os.path.basename(f.path) for f in sources.walk_new([str(tmp_path)], since)}
    assert names == {'video.mp4'}


# ------------------------------------------------------------------ outcome

def test_outcome_carries_files_only_when_there_are_some():
    from cognitrix.tools.utils import ToolOutcome

    plain = ToolOutcome.success('done')
    assert 'files' not in plain.model_dump(exclude_none=True) or not plain.model_dump().get('files')
    ref = {'id': 'f1', 'name': 'chart.png', 'kind': 'image'}
    shared = ToolOutcome.success('Shared 1 file', files=[ref])
    assert shared.files == [ref]
    assert 'chart.png' in shared.model_content()


# ------------------------------------------------------------------ share

async def test_share_file_copies_in_and_returns_a_card(db):
    from cognitrix.produced_files import service, storage
    from cognitrix.produced_files.models import ProducedFile
    from cognitrix.tools.utils import reset_execution_context

    _png(db / 'chart.png')
    token = _as('u1', db, session_id='s1', agent_id='a1')
    try:
        outcome = await share_file(['chart.png', 'missing.png'], caption='The chart')
    finally:
        reset_execution_context(token)
    assert outcome.status == 'success'
    assert len(outcome.files) == 1
    card = outcome.files[0]
    assert card['name'] == 'chart.png' and card['kind'] == 'image' and card['shared'] and card['shown']
    assert card['caption'] == 'The chart'
    assert card['info']['width'] == 64
    assert any('missing.png' in w for w in outcome.warnings)

    row = await ProducedFile.get(card['id'])
    assert row.owner_id == 'u1' and row.session_id == 's1' and row.stored
    copy = storage.resolve(row.storage_key)
    assert copy.is_file() and copy.read_bytes() == (db / 'chart.png').read_bytes()
    assert row.thumb_key and storage.resolve(row.thumb_key).is_file()
    assert [r.id for r in await service.for_chat('u1', session_id='s1')] == [row.id]
    assert await service.for_chat('someone-else', session_id='s1') == []


async def test_share_refuses_paths_outside_the_folders(db, tmp_path):
    from cognitrix.tools.utils import reset_execution_context

    outside = tmp_path / 'secret.png'
    _png(outside)
    token = _as('u1', db, session_id='s1')
    try:
        outcome = await share_file([str(outside)])
    finally:
        reset_execution_context(token)
    assert outcome.status != 'success'
    assert not outcome.files


# ------------------------------------------------------------------ end of turn

async def test_files_made_promotes_likely_results_and_shelves_the_rest(db):
    from cognitrix.common import folders as F
    from cognitrix.produced_files import service

    since = time.time() - 2
    _png(db / 'poster.png')
    (db / 'notes.txt').write_text('some notes')
    (db / 'helper.py').write_text('print(1)')
    (db / '.cache').mkdir()
    (db / '.cache' / 'x.png').write_bytes(b'x' * 9000)

    refs = await service.files_made(owner_id='u1', agent_id='a1', session_id='s1', task_id='', run_id='',
                                    since=since, folders=[F.Folder(str(db), F.WRITE, 'work', True)])
    by_name = {r['name']: r for r in refs}
    assert set(by_name) == {'poster.png', 'notes.txt'}
    assert by_name['poster.png']['shown'] and not by_name['poster.png']['shared']
    assert not by_name['notes.txt']['shown']

    # The next turn doesn't list the same files again unless they changed.
    again = await service.files_made(owner_id='u1', agent_id='a1', session_id='s1', task_id='', run_id='',
                                     since=time.time() + 1, folders=[F.Folder(str(db), F.WRITE, 'work', True)])
    assert again == []


async def test_files_made_skips_what_the_agent_shared(db):
    from cognitrix.common import folders as F
    from cognitrix.produced_files import service
    from cognitrix.tools.utils import reset_execution_context

    since = time.time() - 2
    service.turn_started(service.turn_key('s2', ''), since)
    _png(db / 'a.png')
    _png(db / 'b.png')
    token = _as('u1', db, session_id='s2')
    try:
        await share_file(['a.png'])
    finally:
        reset_execution_context(token)
    refs = await service.files_made(owner_id='u1', agent_id='', session_id='s2', task_id='', run_id='',
                                    since=since, folders=[F.Folder(str(db), F.WRITE, 'work', True)])
    names = [(r['name'], r['shared'], r['shown']) for r in refs]
    # The shared one is a card; the other stays on the shelf because something was shared.
    assert names == [('a.png', True, True), ('b.png', False, False)]


# ------------------------------------------------------------------ API

async def test_signed_links_and_ownership(db):
    from fastapi import HTTPException

    from cognitrix.api.routes import produced_files as api
    from cognitrix.common.security import AuthContext
    from cognitrix.models import User
    from cognitrix.session_ownership import principal_key
    from cognitrix.tools.utils import reset_execution_context

    await User(name='Ann', email='ann@example.com', password='x' * 20).save()
    ann = await User.find_one({'email': 'ann@example.com'})
    await User(name='Bob', email='bob@example.com', password='x' * 20).save()
    bob = await User.find_one({'email': 'bob@example.com'})

    _png(db / 'pic.png')
    (db / 'page.html').write_text('<script>alert(1)</script>')
    token = _as(principal_key(ann), db, session_id='s1')
    try:
        outcome = await share_file(['pic.png', 'page.html'])
    finally:
        reset_execution_context(token)
    pic, page = outcome.files

    details = await api.get_file(pic['id'], ctx=AuthContext(user=ann))
    assert details['available'] and details['url'].startswith('/api/v1/produced-file-content/')
    with pytest.raises(HTTPException) as err:
        await api.get_file(pic['id'], ctx=AuthContext(user=bob))
    assert err.value.status_code == 404

    content_token = details['url'].rsplit('/', 1)[1]
    res = await api.content(content_token)
    assert res.media_type == 'image/png'
    assert 'inline' in res.headers['content-disposition']
    # A content link isn't a thumbnail link.
    with pytest.raises(HTTPException):
        await api.thumb(content_token)
    thumb_token = details['thumb_url'].rsplit('/', 1)[1]
    assert (await api.thumb(thumb_token)).media_type == 'image/jpeg'

    # HTML is never opened in place.
    page_details = await api.get_file(page['id'], ctx=AuthContext(user=ann))
    res = await api.content(page_details['url'].rsplit('/', 1)[1])
    assert res.media_type == 'application/octet-stream'
    assert 'attachment' in res.headers['content-disposition']
    assert 'sandbox' in res.headers['content-security-policy']

    with pytest.raises(HTTPException):
        await api.content('not-a-token')

    zipped = await api.zip_files(api.ZipBody(ids=[pic['id'], page['id']], name='both'), ctx=AuthContext(user=ann))
    assert zipped.media_type == 'application/zip' and len(zipped.body) > 100
    with pytest.raises(HTTPException):
        await api.zip_files(api.ZipBody(ids=[pic['id']]), ctx=AuthContext(user=bob))

    assert (await api.forget(pic['id'], ctx=AuthContext(user=ann))) == {'removed': True}
    with pytest.raises(HTTPException):
        await api.content(content_token)



# ------------------------------------------------------------------ review fixes

async def test_a_file_named_like_the_thumbnail_keeps_its_own_copy(db):
    from PIL import Image

    from cognitrix.produced_files import storage
    from cognitrix.produced_files.models import ProducedFile
    from cognitrix.tools.utils import reset_execution_context

    original = db / 'thumb.jpg'
    Image.new('RGB', (1600, 1200), (10, 120, 200)).save(original, quality=95)
    token = _as('u1', db, session_id='s1')
    try:
        outcome = await share_file(['thumb.jpg'])
    finally:
        reset_execution_context(token)
    row = await ProducedFile.get(outcome.files[0]['id'])
    assert row.storage_key != row.thumb_key
    assert storage.resolve(row.storage_key).read_bytes() == original.read_bytes()


def _xlsx(path: Path) -> Path:
    import zipfile

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook {ns} {rel_ns}><sheets>'
                   '<sheet name="Sales" sheetId="1" r:id="rId1"/><sheet name="Notes" sheetId="2" r:id="rId2"/>'
                   '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="/xl/worksheets/other.xml"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', f'<sst {ns}><si><t>Region</t></si><si><t>Total</t></si>'
                   '<si><r><t>No</t></r><r><t>rth</t></r></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet {ns}><dimension ref="A1:B40"/><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>12.0</v></c></row>'
                   '<row r="4"><c r="B4" t="b"><v>1</v></c></row>'
                   '</sheetData></worksheet>')
        z.writestr('xl/worksheets/other.xml', f'<worksheet {ns}><sheetData>'
                   '<row r="1"><c r="C1" t="inlineStr"><is><t>hi</t></is></c></row></sheetData></worksheet>')
    return path


def test_excel_preview_needs_no_extra_library(tmp_path):
    from cognitrix.produced_files import previews

    sheets = previews.read_sheets(_xlsx(tmp_path / 'sales.xlsx'))
    assert [s['name'] for s in sheets] == ['Sales', 'Notes']
    assert sheets[0]['rows'] == [['Region', 'Total'], ['North', '12'], ['', ''], ['', 'TRUE']]
    assert sheets[0]['total_rows'] == 40
    assert sheets[1]['rows'] == [['', '', 'hi']]
    info, _ = previews.make(tmp_path / 'sales.xlsx', 'sheet')
    assert info['sheets'] == 2 and info['preview'][0] == ['Region', 'Total']


async def test_a_later_sandbox_fetch_uses_the_chats_project(db, monkeypatch):
    from cognitrix.common import sandbox_work
    from cognitrix.produced_files import service, sources
    from cognitrix.produced_files.models import SANDBOX, ProducedFile
    from cognitrix.projects import context

    seen = {}

    class FakeWork:
        def __init__(self, user_id, agent_id):
            seen['project'] = context.get()

    def fake_fetch(ws, path, dest, max_bytes):
        dest.write_bytes(b'data')
        return 4

    async def fake_for_session(session, user_id=None):
        seen['session'] = session
        return 'the-project'

    monkeypatch.setattr(sandbox_work, 'SandboxWork', FakeWork)
    monkeypatch.setattr(sources, 'sandbox_fetch', fake_fetch)
    monkeypatch.setattr(context, 'for_session', fake_for_session)
    row = ProducedFile(owner_id='u1', agent_id='a1', task_id='t1', name='out.csv', path='/home/agent/work/out.csv',
                       source=SANDBOX, mime_type='text/csv', kind='sheet', size_bytes=4, created=service.now())
    await row.save()
    path = await service.local_path(row)
    assert path.read_bytes() == b'data'
    assert seen['project'] == 'the-project'
    assert seen['session'].task_id == 't1'


# ------------------------------------------------------------------ screenshots

async def test_show_screenshot_puts_the_window_in_the_chat(db, monkeypatch):
    from PIL import Image

    from cognitrix.computer.sandbox import client as sandbox_client
    from cognitrix.produced_files import storage
    from cognitrix.produced_files.models import ProducedFile
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import reset_execution_context

    class Driver:
        backend = SimpleNamespace(window=lambda wid: SimpleNamespace(title='My app'))

        def look(self, window_id, query, limit, viewer=None):
            assert window_id == 7
            return Image.new('RGB', (320, 200), (0, 128, 0)), None, [], ''

    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    monkeypatch.setattr(sandbox_client, 'sandbox_driver', lambda actor=None: Driver())
    tool = ToolManager.get_by_name('Show Screenshot')
    token = _as('u1', db, session_id='s1', agent_id='a1')
    try:
        outcome = (await tool.run(window_id=7, caption='The new page')).outcome
    finally:
        reset_execution_context(token)
    assert outcome.status == 'success', outcome
    assert "'My app'" in outcome.text
    card = outcome.files[0]
    assert card['kind'] == 'image' and card['shared'] and card['shown'] and card['caption'] == 'The new page'
    assert card['info']['width'] == 320
    row = await ProducedFile.get(card['id'])
    assert row.session_id == 's1' and row.path == ''
    assert storage.resolve(row.storage_key).read_bytes()[:4] == b'\x89PNG'


async def test_show_screenshot_needs_the_computer(db, monkeypatch):
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import reset_execution_context

    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    token = _as('u1', db, session_id='s1')
    try:
        outcome = (await ToolManager.get_by_name('Show Screenshot').run(window_id=1)).outcome
    finally:
        reset_execution_context(token)
    assert outcome.status != 'success' and not outcome.files


async def test_a_screenshot_that_was_cleared_out_says_so(db):
    from cognitrix.produced_files import service
    from cognitrix.produced_files.models import SERVER, ProducedFile
    from cognitrix.produced_files.sources import SourceError

    row = ProducedFile(owner_id='u1', name='screenshot.png', path='', source=SERVER, mime_type='image/png',
                       kind='image', created=service.now())
    await row.save()
    with pytest.raises(SourceError, match='no longer kept'):
        await service.local_path(row)


async def test_a_screenshot_doesnt_hand_the_agent_new_control_numbers(db, monkeypatch):
    """Show Screenshot looks under its own viewer: the agent's next action on
    old numbers is refused as out of date instead of hitting another control."""
    from cognitrix.computer import Driver
    from cognitrix.computer.sandbox import client as sandbox_client
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import reset_execution_context
    from tests.test_background_computer_use import FakeBackend

    driver = Driver(FakeBackend())
    driver.settle_s = 0
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    monkeypatch.setattr(sandbox_client, 'sandbox_driver', lambda actor=None: driver)
    agent = SimpleNamespace(id='a1', name='Maker')
    token = _as('u1', db, session_id='s1', agent_id='a1')
    try:
        await ToolManager.get_by_name('Look At Window').run(window_id=1, parent=agent)
        shown = (await ToolManager.get_by_name('Show Screenshot').run(window_id=1, parent=agent)).outcome
        click = await ToolManager.get_by_name('Click Element').run(window_id=1, element=0, parent=agent)
    finally:
        reset_execution_context(token)
    assert 'Look at the window again' in shown.text
    assert 'looked at again' in str(click.content)


async def test_show_screenshot_on_a_paired_pc_uses_the_pc(db, monkeypatch):
    import io as _io

    from PIL import Image

    from cognitrix.computer.paired import turn
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import reset_execution_context

    buf = _io.BytesIO()
    Image.new('RGB', (100, 60), (1, 2, 3)).save(buf, format='PNG')
    asked = []

    async def fake_look(window_id):
        asked.append(window_id)
        return 'Window 5 Notes\nControls (1 of 1):\n[0] button "Save"', buf.getvalue()

    monkeypatch.setattr(turn, 'look_on_pc', fake_look)
    token = _as('u1', db, session_id='s1', device='pc-1')
    try:
        outcome = (await ToolManager.get_by_name('Show Screenshot').run(window_id=5, computer='person')).outcome
    finally:
        reset_execution_context(token)
    assert asked == [5]
    assert outcome.status == 'success' and outcome.files[0]['info']['width'] == 100
    assert '[0] button "Save"' in outcome.text
