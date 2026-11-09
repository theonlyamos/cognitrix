"""Knowledge base: collections of a person's documents that their agents can
search and quote from.

Each collection and document belongs to one person. A document is read into
text, cut into passages and embedded on this machine; nothing is sent to a
model provider until an agent quotes a passage in a chat.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from cognitrix.knowledge import extract, store
from cognitrix.knowledge.models import KnowledgeCollection, KnowledgeDocument

logger = logging.getLogger('cognitrix.log')

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENTS_PER_COLLECTION = 200
MAX_COLLECTIONS = 50
NAME_MAX = 80

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
    }
