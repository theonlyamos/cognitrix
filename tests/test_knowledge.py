"""Knowledge base: reading files, searching passages, and who may search what."""
import io
import math
import re
import zipfile
from types import SimpleNamespace

import pytest

from cognitrix import knowledge
from cognitrix.knowledge import extract, store
from tests.test_session_ownership_routes import _initialize_routes_db


def _fake_embed(texts):
    """A tiny bag-of-words embedding so tests need no model download."""
    out = []
    for text in texts:
        vector = [0.0] * 64
        for word in re.findall(r'[a-z0-9]+', text.lower()):
            vector[sum(map(ord, word)) % 64] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        out.append([v / norm for v in vector])
    return out


@pytest.fixture
async def kb(tmp_path, monkeypatch):
    import chromadb
    from chromadb.config import Settings

    from cognitrix.knowledge.models import KnowledgeCollection, KnowledgeDocument

    await _initialize_routes_db(tmp_path)
    await KnowledgeCollection._create_table_async()
    await KnowledgeDocument._create_table_async()
    client = chromadb.PersistentClient(path=str(tmp_path / 'kb'), settings=Settings(anonymized_telemetry=False))
    monkeypatch.setattr(store, '_client', client)
    monkeypatch.setattr(store, '_embed', _fake_embed)
    return tmp_path


def _docx(*paragraphs):
    body = ''.join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return buffer.getvalue()


def test_text_comes_out_of_each_supported_kind():
    assert extract.to_text('notes.md', b'# Title\n\nHello there') == '# Title\n\nHello there'
    assert extract.to_text('letter.docx', _docx('Dear Ada,', 'The invoice is attached.')) == 'Dear Ada,\n\nThe invoice is attached.'
    html = b'<html><head><style>p{}</style><script>x()</script></head><body><p>Visible</p></body></html>'
    assert extract.to_text('page.html', html) == 'Visible'

    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), 'Quarterly sales rose ten percent.')
    assert 'Quarterly sales rose ten percent.' in extract.to_text('report.pdf', doc.tobytes())

    for name, data in (('photo.png', b'x'), ('empty.txt', b'   '), ('broken.docx', b'not a zip')):
        with pytest.raises(extract.ExtractError):
            extract.to_text(name, data)


def test_passages_overlap_and_stay_near_the_size():
    text = '\n\n'.join(f'Paragraph {i}. ' + 'word ' * 60 for i in range(12))
    pieces = extract.passages(text, size=500, overlap=80)
    assert len(pieces) > 5 and all(len(piece) <= 700 for piece in pieces)
    assert 'Paragraph 0.' in pieces[0] and 'Paragraph 11.' in pieces[-1]


async def test_documents_are_searchable_and_private(kb):
    mine = await knowledge.create_collection('user-1', 'Handbook')
    theirs = await knowledge.create_collection('user-2', 'Private')
    doc = await knowledge.add_document(
        mine, 'leave.md', b'Annual leave is 25 days.\n\nSick leave needs a doctor note after 3 days.', wait=True)
    await knowledge.add_document(theirs, 'salaries.txt', b'Annual salaries are confidential.', wait=True)
    assert doc.status == 'ready' and doc.passages >= 1

    hits = await knowledge.search('user-1', [str(mine.id), str(theirs.id)], 'how many days of annual leave')
    assert hits and hits[0]['name'] == 'leave.md' and '25 days' in hits[0]['text']
    assert all(hit['collection'] == 'Handbook' for hit in hits)
    # Naming someone else's collection finds nothing.
    assert await knowledge.search('user-1', [str(theirs.id)], 'salaries') == []

    await knowledge.delete_document(doc)
    assert await knowledge.search('user-1', [str(mine.id)], 'annual leave') == []


async def test_a_file_that_cannot_be_read_is_marked_failed(kb):
    collection = await knowledge.create_collection('user-1', 'Scans')
    doc = await knowledge.add_document(collection, 'scan.pdf', b'not really a pdf', wait=True)
    assert doc.status == 'failed' and 'could not be read' in doc.error
    with pytest.raises(knowledge.KnowledgeError):
        await knowledge.add_document(collection, 'movie.mp4', b'x', wait=True)
    with pytest.raises(knowledge.KnowledgeError):
        await knowledge.create_collection('user-1', 'scans')


async def test_tool_searches_only_what_the_agent_was_given(kb):
    from cognitrix.tools.knowledge import search_knowledge

    handbook = await knowledge.create_collection('user-1', 'Handbook')
    other = await knowledge.create_collection('user-1', 'Recipes')
    await knowledge.add_document(handbook, 'leave.md', b'Annual leave is 25 days.', wait=True)
    await knowledge.add_document(other, 'soup.md', b'Leek soup needs three leeks.', wait=True)

    agent = SimpleNamespace(owner_id='user-1', knowledge=[str(handbook.id)])
    result = await search_knowledge.run(query='annual leave', parent=agent)
    assert 'leave.md' in str(result) and '25 days' in str(result)
    assert 'soup' not in str(await search_knowledge.run(query='leek soup', parent=agent))

    nothing = await search_knowledge.run(query='leave', parent=SimpleNamespace(owner_id='user-1', knowledge=[]))
    assert 'no document collections' in str(nothing)


async def test_a_document_deleted_while_being_read_leaves_no_passages(kb, monkeypatch):
    """Delete during ingestion: nothing of the document may stay searchable."""
    from cognitrix import knowledge
    from cognitrix.knowledge import store
    from cognitrix.knowledge.models import KnowledgeDocument

    collection = await knowledge.create_collection('u1', 'Notes')
    real_add = store.add

    async def add_after_delete(collection_id, document_id, name, pieces):
        await KnowledgeDocument.delete_many({'id': document_id})
        return await real_add(collection_id, document_id, name, pieces)

    monkeypatch.setattr(store, 'add', add_after_delete)
    await knowledge.add_document(collection, 'gone.txt', b'The vault code is 4417.', wait=True)
    assert await knowledge.search('u1', [str(collection.id)], 'vault code') == []


# --- web sources ---------------------------------------------------------------

SITE = {
    '/docs/': ('text/html', '<html><head><title>Widget docs</title></head><body><nav>Menu Home About</nav>'
                '<main><h1>Widget docs</h1><p>Widgets ship in three sizes.</p>'
                '<a href="install.html">Install</a> <a href="/blog/post">Blog</a> <a href="logo.png">logo</a>'
                '<a href="secret/">Secret</a></main><footer>Copyright</footer></body></html>'),
    '/docs/install.html': ('text/html', '<html><head><title>Install</title></head><body>'
                           '<p>Run pip install widget to set it up.</p><a href="/docs/">Back</a></body></html>'),
    '/docs/secret/': ('text/html', '<html><body><p>Hidden page text.</p></body></html>'),
    '/blog/post': ('text/html', '<html><body><p>Off-topic blog post about lunch.</p></body></html>'),
    '/robots.txt': ('text/plain', 'User-agent: *\nDisallow: /docs/secret/\n'),
    '/notes.txt': ('text/plain', 'Plain text notes about gadgets.'),
    '/old': None,  # redirect to /docs/
}


@pytest.fixture
def site(monkeypatch):
    """A small website on localhost (private addresses allowed for the test)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cognitrix.knowledge import web

    pages = dict(SITE)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == '/old':
                self.send_response(301)
                self.send_header('Location', '/docs/')
                self.end_headers()
                return
            entry = pages.get(self.path)
            if entry is None:
                self.send_response(404)
                self.end_headers()
                return
            kind, body = entry
            data = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', f'{kind}; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', '1')
    monkeypatch.setattr(web, 'CRAWL_DELAY', 0)
    yield SimpleNamespace(base=f'http://127.0.0.1:{server.server_port}', pages=pages)
    server.shutdown()


def test_web_addresses_are_checked():
    from cognitrix.knowledge import web

    assert web.normalize_url('example.com/a#top') == 'https://example.com/a'
    for bad in ('', 'ftp://example.com/x', 'https://user:pw@example.com/', 'javascript:alert(1)'):
        with pytest.raises(web.WebError):
            web.normalize_url(bad)


async def test_private_addresses_are_refused(monkeypatch):
    from cognitrix.knowledge import web

    monkeypatch.delenv('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', raising=False)
    for url in ('http://127.0.0.1:9/', 'http://169.254.169.254/latest/meta-data/', 'http://localhost/'):
        with pytest.raises(web.WebError) as err:
            await web.read_site(url)
        assert 'private' in str(err.value) or 'resolve' in str(err.value)


async def test_one_page_is_read_without_menus(site):
    from cognitrix.knowledge import web

    result = await web.read_site(site.base + '/docs/')
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.title == 'Widget docs' and 'three sizes' in page.text
    assert 'Menu' not in page.text and 'Copyright' not in page.text


async def test_a_site_section_is_crawled_within_its_folder_and_robots(site):
    from cognitrix.knowledge import web

    result = await web.read_site(site.base + '/old', max_pages=10)
    urls = {page.url for page in result.pages}
    # Followed the redirect, read the linked page in the same folder, skipped
    # the blog (outside /docs/), the image and the robots.txt-disallowed page.
    assert urls == {site.base + '/docs/', site.base + '/docs/install.html'}
    assert result.skipped == 1


async def test_web_source_is_searchable_refreshable_and_private(kb, site):
    collection = await knowledge.create_collection('user-1', 'Widget')
    doc = await knowledge.add_url(collection, site.base + '/docs/', max_pages=5, wait=True)
    assert doc.status == 'ready' and doc.pages_read == 2 and doc.source_url == site.base + '/docs/'

    hits = await knowledge.search('user-1', [str(collection.id)], 'pip install widget')
    assert hits and hits[0]['url'] == site.base + '/docs/install.html'
    assert await knowledge.search('user-2', [str(collection.id)], 'pip install widget') == []

    with pytest.raises(knowledge.KnowledgeError):
        await knowledge.add_url(collection, site.base + '/docs/')

    # The site changes; a refresh swaps the passages.
    site.pages['/docs/install.html'] = ('text/html', '<html><body><p>Use uv add widget now.</p></body></html>')
    await knowledge.refresh_document(doc, wait=True)
    assert await knowledge.search('user-1', [str(collection.id)], 'uv add widget')
    texts = ' '.join(hit['text'] for hit in await knowledge.search('user-1', [str(collection.id)], 'pip install', 8))
    assert 'pip install' not in texts

    # A refresh that fails keeps the last good passages.
    site.pages.pop('/docs/')
    refreshed = await knowledge.refresh_document(doc, wait=True)
    assert refreshed.status == 'ready' and refreshed.error.startswith('Last refresh failed')
    assert await knowledge.search('user-1', [str(collection.id)], 'uv add widget')

    with pytest.raises(knowledge.KnowledgeError):
        await knowledge.refresh_document(await knowledge.add_document(collection, 'a.txt', b'Text', wait=True))


async def test_plain_text_and_failed_addresses(kb, site):
    collection = await knowledge.create_collection('user-1', 'Misc')
    doc = await knowledge.add_url(collection, site.base + '/notes.txt', wait=True)
    assert doc.status == 'ready' and doc.passages == 1
    missing = await knowledge.add_url(collection, site.base + '/nope', wait=True)
    assert missing.status == 'failed' and '404' in missing.error
    with pytest.raises(knowledge.KnowledgeError):
        await knowledge.add_url(collection, site.base + '/notes.txt', max_pages=99)


async def test_web_sources_refresh_when_due(kb, site):
    from datetime import UTC, datetime, timedelta

    from cognitrix.knowledge.models import KnowledgeDocument

    collection = await knowledge.create_collection('user-1', 'Daily')
    doc = await knowledge.add_url(collection, site.base + '/notes.txt', refresh_days=1, wait=True)
    once = await knowledge.add_url(collection, site.base + '/docs/install.html', wait=True)
    assert await knowledge.refresh_due() == 0
    later = datetime.now(UTC) + timedelta(days=1, minutes=1)
    assert await knowledge.refresh_due(later) == 1
    assert (await KnowledgeDocument.get(doc.id)).status == 'ready'
    with pytest.raises(knowledge.KnowledgeError):
        await knowledge.update_web_source(once, refresh_days=3)
    assert (await knowledge.update_web_source(once, refresh_days=7)).refresh_days == 7


async def test_agent_learns_a_website_into_its_own_collection(kb, site):
    from cognitrix.tools.knowledge import learn_from_website, search_knowledge

    handbook = await knowledge.create_collection('user-1', 'Handbook')
    other = await knowledge.create_collection('user-1', 'Other')
    stranger = await knowledge.create_collection('user-2', 'Theirs')
    agent = SimpleNamespace(owner_id='user-1', knowledge=[str(handbook.id), str(other.id), str(stranger.id)])

    assert 'Say which collection' in str(await learn_from_website.run(url=site.base + '/docs/', parent=agent))
    assert 'No collection' in str(await learn_from_website.run(url=site.base + '/docs/', collection='Theirs', parent=agent))
    result = await learn_from_website.run(url=site.base + '/docs/', collection='handbook', max_pages=5, parent=agent)
    assert 'Added "Widget docs" to Handbook: 2 pages' in str(result)
    found = str(await search_knowledge.run(query='pip install widget', parent=agent))
    assert 'page: ' + site.base + '/docs/install.html' in found


async def test_web_source_api(kb, site):
    from fastapi import HTTPException

    from cognitrix.api.routes import knowledge as routes
    from cognitrix.common.security import AuthContext

    owner = SimpleNamespace(id='user-1', email='u1@example.com', role='admin')
    other = SimpleNamespace(id='user-2', email='u2@example.com', role='member')
    ctx, other_ctx = AuthContext(user=owner), AuthContext(user=other)
    created = await routes.create_collection(routes.CollectionBody(name='Docs'), ctx)
    doc = await routes.add_url(created['id'], routes.UrlBody(url=site.base + '/notes.txt'), ctx)
    assert doc['source_url'] == site.base + '/notes.txt' and doc['status'] == 'queued'
    with pytest.raises(HTTPException) as err:
        await routes.refresh_document(created['id'], doc['id'], other_ctx)
    assert err.value.status_code == 404
    with pytest.raises(HTTPException):
        await routes.add_url(created['id'], routes.UrlBody(url='ftp://x'), ctx)


async def test_a_refresh_that_fails_while_indexing_keeps_the_old_passages(kb, site, monkeypatch):
    collection = await knowledge.create_collection('user-1', 'Widget')
    doc = await knowledge.add_url(collection, site.base + '/docs/install.html', wait=True)
    assert await knowledge.search('user-1', [str(collection.id)], 'pip install widget')

    site.pages['/docs/install.html'] = ('text/html', '<html><body><p>Brand new text.</p></body></html>')

    def broken_embed(texts):
        raise RuntimeError('embedding model unavailable')

    monkeypatch.setattr(store, '_embed', broken_embed)
    refreshed = await knowledge.refresh_document(doc, wait=True)
    assert refreshed.status == 'ready' and refreshed.error.startswith('Last refresh failed')
    monkeypatch.setattr(store, '_embed', _fake_embed)
    hits = await knowledge.search('user-1', [str(collection.id)], 'pip install widget')
    assert hits and 'pip install' in hits[0]['text']


async def test_interrupted_reads_are_picked_up_once_old_enough(kb, site, monkeypatch):
    from cognitrix.knowledge.models import KnowledgeDocument

    collection = await knowledge.create_collection('user-1', 'Widget')
    row = KnowledgeDocument(collection_id=str(collection.id), user_id='user-1', name='x',
                            created=knowledge._now(), source_url=site.base + '/notes.txt')
    await row.save()
    stuck = await KnowledgeDocument.find_one({'source_url': site.base + '/notes.txt'})
    started = []
    monkeypatch.setattr(knowledge, '_start', lambda job: (started.append(1), job.close()))
    await knowledge.resume_interrupted()
    assert started == []  # too recent: maybe another process is reading it
    await knowledge.resume_interrupted(min_age_seconds=0)
    assert started == [1]
    assert stuck.status == 'queued'
