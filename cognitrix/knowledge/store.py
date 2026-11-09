"""Where passages are kept and searched: one Chroma collection per knowledge
collection, embedded locally (the same small model the app already ships)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger('cognitrix.log')

_client = None
_BATCH = 64


def _chroma():
    global _client
    if _client is None:
        import chromadb
        from chromadb.config import Settings

        from cognitrix.config import settings

        _client = chromadb.PersistentClient(
            path=str(settings.workdir / 'knowledge_db'),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _embed(texts: list[str]) -> list[list[float]]:
    from cognitrix.utils.embedding_model import get_embedding_model

    model = get_embedding_model()
    return [model.encode(text).tolist() for text in texts]


def _collection(collection_id: str):
    name = 'kb_' + re.sub(r'[^a-zA-Z0-9_-]', '_', str(collection_id))[:55]
    return _chroma().get_or_create_collection(name=name, metadata={'hnsw:space': 'cosine'})


def _add(collection_id: str, document_id: str, name: str, pieces: list[str]) -> None:
    collection = _collection(collection_id)
    for start in range(0, len(pieces), _BATCH):
        batch = pieces[start:start + _BATCH]
        collection.add(
            ids=[f'{document_id}:{start + offset}' for offset in range(len(batch))],
            documents=batch,
            embeddings=_embed(batch),
            metadatas=[
                {'document_id': str(document_id), 'name': name, 'position': start + offset}
                for offset in range(len(batch))
            ],
        )


def _remove_document(collection_id: str, document_id: str) -> None:
    _collection(collection_id).delete(where={'document_id': str(document_id)})


def _remove_collection(collection_id: str) -> None:
    try:
        _chroma().delete_collection(_collection(collection_id).name)
    except Exception:
        logger.debug('Could not delete knowledge collection %s', collection_id, exc_info=True)


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r'[a-z0-9]+', text.lower()) if len(word) > 2}


def _search(collection_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    collection = _collection(collection_id)
    total = collection.count()
    if not total:
        return []
    found = collection.query(
        query_embeddings=_embed([query]),
        n_results=min(total, max(limit * 3, 12)),
        include=['documents', 'metadatas', 'distances'],
    )
    wanted = _words(query)
    hits = []
    for text, meta, distance in zip(found['documents'][0], found['metadatas'][0], found['distances'][0], strict=False):
        similarity = 1.0 - float(distance)
        overlap = len(wanted & _words(text)) / len(wanted) if wanted else 0.0
        hits.append({
            'text': text,
            'document_id': meta.get('document_id'),
            'name': meta.get('name'),
            'position': int(meta.get('position') or 0),
            'score': round(similarity + 0.25 * overlap, 4),
        })
    hits.sort(key=lambda hit: -hit['score'])
    return hits[:limit]


async def _threaded(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


async def add(collection_id: str, document_id: str, name: str, pieces: list[str]) -> None:
    await _threaded(_add, collection_id, document_id, name, pieces)


async def remove_document(collection_id: str, document_id: str) -> None:
    await _threaded(_remove_document, collection_id, document_id)


async def remove_collection(collection_id: str) -> None:
    await _threaded(_remove_collection, collection_id)


async def search(collection_id: str, query: str, limit: int = 6) -> list[dict[str, Any]]:
    return await _threaded(_search, collection_id, query, limit)
