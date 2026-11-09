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
