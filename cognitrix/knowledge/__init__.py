"""Knowledge base: collections of a person's documents that their agents can
search and quote from.

Each collection and document belongs to one person. A document is read into
text, cut into passages and embedded on this machine; nothing is sent to a
model provider until an agent quotes a passage in a chat.

A document can also be a web source (``source_url``): one page, or a page and
the pages it links to on the same site (``knowledge/web.py``). Web sources can
be read again on request or every few days (``refresh_due``).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from cognitrix.knowledge import extract, store, web
from cognitrix.knowledge.models import KnowledgeCollection, KnowledgeDocument

logger = logging.getLogger('cognitrix.log')

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENTS_PER_COLLECTION = 200
MAX_COLLECTIONS = 50
NAME_MAX = 80
REFRESH_CHOICES = (0, 1, 7, 30)
"""Days between automatic re-reads of a web source (0 = only when asked)."""

_jobs: set[asyncio.Task] = set()


class KnowledgeError(ValueError):
    """A request that can't be done; the message is safe to show."""


def _now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')


async def collections(user_id: str) -> list[KnowledgeCollection]:
    rows = list(await KnowledgeCollection.find({'user_id': str(user_id)}))
    return sorted(rows, key=lambda row: row.name.lower())


async def get_collection(user_id: str, collection_id: str) -> KnowledgeCollection | None:
    row = await KnowledgeCollection.get(str(collection_id or '').strip()) if collection_id else None
    return row if row is not None and str(row.user_id) == str(user_id) else None


async def create_collection(user_id: str, name: str, description: str = '') -> KnowledgeCollection:
    name = ' '.join(str(name or '').split())[:NAME_MAX]
    if not name:
        raise KnowledgeError('Give the collection a name')
    existing = await collections(user_id)
    if len(existing) >= MAX_COLLECTIONS:
        raise KnowledgeError(f'You can have up to {MAX_COLLECTIONS} collections')
    if any(row.name.lower() == name.lower() for row in existing):
        raise KnowledgeError('You already have a collection with that name')
    row = KnowledgeCollection(user_id=str(user_id), name=name, description=str(description or '').strip()[:300],
                              created=_now())
    await row.save()
    return await KnowledgeCollection.find_one({'user_id': str(user_id), 'name': name}) or row


async def delete_collection(row: KnowledgeCollection) -> None:
    await KnowledgeDocument.delete_many({'collection_id': str(row.id)})
    await store.remove_collection(str(row.id))
    await KnowledgeCollection.delete_many({'id': row.id})


async def documents(collection_id: str) -> list[KnowledgeDocument]:
    rows = list(await KnowledgeDocument.find({'collection_id': str(collection_id)}))
    return sorted(rows, key=lambda row: row.created, reverse=True)


async def _ingest(document_id: str, collection_id: str, name: str, data: bytes) -> None:
    try:
        text = await asyncio.get_running_loop().run_in_executor(None, extract.to_text, name, data)
        pieces = extract.passages(text)
        await store.add(collection_id, document_id, name, pieces)
        # Deleted while it was being read: take the passages back out, or
        # agents could still quote a document nobody can see or remove.
        if await KnowledgeDocument.get(document_id) is None:
            if await KnowledgeCollection.get(collection_id) is None:
                await store.remove_collection(collection_id)
            else:
                await store.remove_document(collection_id, document_id)
            return
        await KnowledgeDocument.update_one({'id': document_id}, {
            'status': 'ready', 'error': '', 'passages': len(pieces), 'characters': len(text),
        })
    except extract.ExtractError as exc:
        await KnowledgeDocument.update_one({'id': document_id}, {'status': 'failed', 'error': str(exc)})
    except Exception:
        logger.exception('Could not add %s to the knowledge base', name)
        await KnowledgeDocument.update_one({'id': document_id}, {
            'status': 'failed', 'error': 'Something went wrong reading this file',
        })


async def add_document(collection: KnowledgeCollection, name: str, data: bytes, *,
                       wait: bool = False) -> KnowledgeDocument:
    """Record the document and read it in the background (``wait`` for tests)."""
    from pathlib import PurePath

    name = PurePath(str(name or 'document')).name[:160] or 'document'
    if PurePath(name).suffix.lower() not in extract.SUPPORTED:
        raise KnowledgeError('Use a PDF, Word (.docx), text, Markdown, CSV or HTML file')
    if not data:
        raise KnowledgeError('That file is empty')
    if len(data) > MAX_DOCUMENT_BYTES:
        raise KnowledgeError('Files can be up to 20 MB')
    if len(await documents(str(collection.id))) >= MAX_DOCUMENTS_PER_COLLECTION:
        raise KnowledgeError(f'A collection can hold up to {MAX_DOCUMENTS_PER_COLLECTION} documents')
    stamp = _now()
    row = KnowledgeDocument(collection_id=str(collection.id), user_id=str(collection.user_id), name=name,
                            size_bytes=len(data), created=stamp)
    await row.save()
    saved = await KnowledgeDocument.find_one({'collection_id': str(collection.id), 'name': name, 'created': stamp}) or row
    job = _ingest(str(saved.id), str(collection.id), name, data)
    if wait:
        await job
        return await KnowledgeDocument.get(saved.id) or saved
    task = asyncio.get_running_loop().create_task(job)
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return saved


async def delete_document(row: KnowledgeDocument) -> None:
    await store.remove_document(str(row.collection_id), str(row.id))
    await KnowledgeDocument.delete_many({'id': row.id})


async def search(user_id: str, collection_ids: list[str], query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Best passages across the person's collections named in ``collection_ids``."""
    query = str(query or '').strip()
    if not query:
        return []
    owned = {str(row.id): row.name for row in await collections(user_id)}
    hits: list[dict[str, Any]] = []
    for collection_id in collection_ids:
        if str(collection_id) not in owned:
            continue
        for hit in await store.search(str(collection_id), query, limit):
            hits.append({**hit, 'collection_id': str(collection_id), 'collection': owned[str(collection_id)]})
    hits.sort(key=lambda hit: -hit['score'])
    return hits[:limit]


def project_document(row: KnowledgeDocument) -> dict[str, Any]:
    return {
        'id': str(row.id), 'name': row.name, 'size_bytes': row.size_bytes, 'status': row.status,
        'error': row.error or None, 'passages': row.passages, 'characters': row.characters, 'created': row.created,
        'source_url': row.source_url or None, 'max_pages': row.max_pages, 'pages_read': row.pages_read,
        'refresh_days': row.refresh_days, 'refreshed': row.refreshed or None,
    }


# ---------------------------------------------------------------- web sources

def _start(job) -> None:
    task = asyncio.get_running_loop().create_task(job)
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)


def _refresh_days(value: Any) -> int:
    try:
        days = int(value or 0)
    except (TypeError, ValueError):
        days = 0
    if days not in REFRESH_CHOICES:
        raise KnowledgeError('Read it again every day, week or month, or only when you ask')
    return days


async def _ingest_web(document_id: str) -> None:
    """Read a web source and swap its passages for the new ones. The old
    passages stay searchable until the new read has worked."""
    row = await KnowledgeDocument.get(document_id)
    if row is None or not row.source_url:
        return
    collection_id = str(row.collection_id)

    async def progress(pages: int) -> None:
        if row.max_pages > 1:
            await KnowledgeDocument.update_one({'id': document_id}, {'error': f'read {pages} of up to {row.max_pages} pages'})

    try:
        site = await web.read_site(row.source_url, row.max_pages, progress=progress)
        pieces: list[str] = []
        urls: list[str] = []
        characters = 0
        for page in site.pages:
            text = page.text[:extract.MAX_CHARACTERS]
            characters += len(text)
            # Each passage starts with the page's title so a quoted passage
            # still says where it came from.
            for piece in extract.passages(text):
                pieces.append(f'{page.title}\n{piece}' if page.title and page.title != page.url else piece)
                urls.append(page.url)
        if not pieces:
            raise web.WebError('No text was found on that page')
        name = row.name
        if site.pages[0].title and site.pages[0].title != site.pages[0].url:
            name = site.pages[0].title[:160]
        await store.remove_document(collection_id, document_id)
        await store.add(collection_id, document_id, name, pieces, urls)
        if await KnowledgeDocument.get(document_id) is None:
            if await KnowledgeCollection.get(collection_id) is None:
                await store.remove_collection(collection_id)
            else:
                await store.remove_document(collection_id, document_id)
            return
        note = (f'{site.skipped} linked page{"s were" if site.skipped != 1 else " was"} skipped '
                '(not readable or not allowed by the site)') if site.skipped else ''
        await KnowledgeDocument.update_one({'id': document_id}, {
            'status': 'ready', 'error': note, 'passages': len(pieces), 'characters': characters,
            'pages_read': len(site.pages), 'name': name, 'refreshed': _now(), 'size_bytes': characters,
        })
    except web.WebError as exc:
        await _web_failed(document_id, str(exc))
    except Exception:
        logger.exception('Could not read %s into the knowledge base', row.source_url)
        await _web_failed(document_id, 'Something went wrong reading this page')


async def _web_failed(document_id: str, message: str) -> None:
    row = await KnowledgeDocument.get(document_id)
    if row is None:
        return
    # A refresh that fails keeps the passages from the last good read.
    changes: dict[str, Any] = {'error': message, 'refreshed': _now()}
    changes['status'] = 'ready' if row.passages else 'failed'
    if row.passages:
        changes['error'] = f'Last refresh failed: {message}'
    await KnowledgeDocument.update_one({'id': document_id}, changes)


async def add_url(collection: KnowledgeCollection, url: str, *, max_pages: int = 1, refresh_days: int = 0,
                  wait: bool = False) -> KnowledgeDocument:
    """Add a web page (and, with ``max_pages`` > 1, the pages it links to on
    the same site) and read it in the background (``wait`` for tests)."""
    try:
        url = web.normalize_url(url)
    except web.WebError as exc:
        raise KnowledgeError(str(exc)) from exc
    try:
        max_pages = int(max_pages or 1)
    except (TypeError, ValueError):
        max_pages = 1
    if not 1 <= max_pages <= web.MAX_PAGES:
        raise KnowledgeError(f'Read between 1 and {web.MAX_PAGES} pages')
    refresh_days = _refresh_days(refresh_days)
    existing = await documents(str(collection.id))
    if len(existing) >= MAX_DOCUMENTS_PER_COLLECTION:
        raise KnowledgeError(f'A collection can hold up to {MAX_DOCUMENTS_PER_COLLECTION} documents')
    if any(doc.source_url == url for doc in existing):
        raise KnowledgeError('That page is already in this collection. Use Refresh to read it again.')
    stamp = _now()
    parsed = urlparse(url)
    label = (parsed.hostname or url) + (parsed.path if parsed.path not in ('', '/') else '')
    row = KnowledgeDocument(collection_id=str(collection.id), user_id=str(collection.user_id), name=label[:160],
                            created=stamp, source_url=url, max_pages=max_pages, refresh_days=refresh_days)
    await row.save()
    saved = await KnowledgeDocument.find_one({'collection_id': str(collection.id), 'source_url': url}) or row
    job = _ingest_web(str(saved.id))
    if wait:
        await job
        return await KnowledgeDocument.get(saved.id) or saved
    _start(job)
    return saved


async def refresh_document(row: KnowledgeDocument, *, wait: bool = False) -> KnowledgeDocument:
    """Read a web source again."""
    if not row.source_url:
        raise KnowledgeError('Only web pages can be refreshed. Upload a new copy of a file instead.')
    if row.status == 'queued':
        raise KnowledgeError('This page is being read already')
    await KnowledgeDocument.update_one({'id': row.id}, {'status': 'queued', 'error': ''})
    job = _ingest_web(str(row.id))
    if wait:
        await job
    else:
        _start(job)
    return await KnowledgeDocument.get(row.id) or row


async def update_web_source(row: KnowledgeDocument, *, refresh_days: Any = None,
                            max_pages: Any = None) -> KnowledgeDocument:
    if not row.source_url:
        raise KnowledgeError('Only web pages have these settings')
    changes: dict[str, Any] = {}
    if refresh_days is not None:
        changes['refresh_days'] = _refresh_days(refresh_days)
    if max_pages is not None:
        try:
            pages = int(max_pages)
        except (TypeError, ValueError):
            pages = 0
        if not 1 <= pages <= web.MAX_PAGES:
            raise KnowledgeError(f'Read between 1 and {web.MAX_PAGES} pages')
        changes['max_pages'] = pages
    if changes:
        await KnowledgeDocument.update_one({'id': row.id}, changes)
    return await KnowledgeDocument.get(row.id) or row


def _due(row: KnowledgeDocument, now: datetime) -> bool:
    if not row.source_url or row.refresh_days <= 0 or row.status == 'queued':
        return False
    if not row.refreshed:
        return True
    try:
        last = datetime.strptime(row.refreshed, '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
    except ValueError:
        return True
    return (now - last).total_seconds() >= row.refresh_days * 86400


async def refresh_due(now: datetime | None = None) -> int:
    """Re-read every web source whose refresh interval has passed, one at a
    time. Returns how many were read."""
    now = now or datetime.now(UTC)
    count = 0
    for row in await KnowledgeDocument.find({}):
        if _due(row, now):
            await KnowledgeDocument.update_one({'id': row.id}, {'status': 'queued'})
            await _ingest_web(str(row.id))
            count += 1
    return count


def _age_seconds(stamp: str, now: datetime) -> float:
    try:
        return (now - datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)).total_seconds()
    except (TypeError, ValueError):
        return float('inf')


async def resume_interrupted(min_age_seconds: float = 600.0) -> None:
    """Documents left half-read when the engine stopped: web sources are read
    again, files (whose bytes weren't kept) are marked failed. Only rows older
    than ``min_age_seconds``, so another server process's reads in progress
    are left alone."""
    now = datetime.now(UTC)
    for row in await KnowledgeDocument.find({'status': 'queued'}):
        if _age_seconds(row.refreshed or row.created, now) < min_age_seconds:
            continue
        if row.source_url:
            _start(_ingest_web(str(row.id)))
        else:
            await KnowledgeDocument.update_one({'id': row.id}, {
                'status': 'failed', 'error': 'The app stopped while reading this file. Add it again.'})


async def refresher_loop(interval_seconds: float = 3600.0) -> None:
    """Keep web sources up to date (started with the API server)."""
    try:
        await resume_interrupted()
    except Exception:
        logger.exception('Could not resume knowledge reads')
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await refresh_due()
        except Exception:
            logger.exception('Knowledge refresh failed')
