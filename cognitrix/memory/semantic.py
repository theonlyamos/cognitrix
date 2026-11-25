"""Meaning-based matching for saved memories.

Memories used to be matched to a question by shared words, so "my partner"
never found "Married to Ama". This module scores them by meaning with the
small ONNX embedding model the app already ships (``utils/embedding_model``).

Everything here is best-effort: when the model can't load (no network for the
first download, or ``COGNITRIX_MEMORY_EMBEDDINGS=0``), callers get ``None`` and
fall back to word matching.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence

from cognitrix.app_settings import get as app_setting

logger = logging.getLogger('cognitrix.log')

_CACHE_MAX = 4_000
_cache: OrderedDict[str, list[float]] = OrderedDict()
_lock = threading.Lock()
_failed_until = 0.0
_RETRY_AFTER_S = 600.0
TIMEOUT_S = 20.0

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]
_embedder: Embedder | None = None
"""Set by tests (and by ``set_embedder``); otherwise the shared ONNX model."""


def set_embedder(fn: Embedder | None) -> None:
    """Use ``fn`` to embed texts (tests), or None for the default model."""
    global _embedder, _failed_until
    _embedder = fn
    _failed_until = 0.0
    with _lock:
        _cache.clear()


def enabled() -> bool:
    if _embedder is not None:
        return True
    if app_setting('COGNITRIX_MEMORY_EMBEDDINGS', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    return time.monotonic() >= _failed_until


def _default_embed(texts: list[str]) -> list[list[float]]:
    from cognitrix.utils.embedding_model import get_embedding_model

    model = get_embedding_model()
    fn = getattr(model, '_fn', None)
    if fn is not None:
        return [list(map(float, v)) for v in fn(texts)]
    return [list(map(float, model.encode(t))) for t in texts]


def _key(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode('utf-8', 'replace')).hexdigest()


def embed(texts: list[str]) -> list[list[float]] | None:
    """Vectors for ``texts`` (blocking), from the cache where possible. None on failure."""
    global _failed_until
    if not enabled():
        return None
    keys = [_key(t) for t in texts]
    with _lock:
        missing = [(k, t) for k, t in zip(keys, texts) if k not in _cache]
    if missing:
        unique: dict[str, str] = dict(missing)
        try:
            vectors = (_embedder or _default_embed)(list(unique.values()))
        except Exception:
            logger.info('Memory embeddings unavailable; using word matching for now', exc_info=True)
            _failed_until = time.monotonic() + _RETRY_AFTER_S
            return None
        with _lock:
            for k, v in zip(unique.keys(), vectors):
                _cache[k] = [float(x) for x in v]
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    with _lock:
        out = []
        for k in keys:
            vec = _cache.get(k)
            if vec is None:
                return None
            _cache.move_to_end(k)
            out.append(vec)
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


async def scores(query: str, texts: list[str]) -> list[float] | None:
    """How close each text is to ``query`` in meaning (cosine, -1..1), or None."""
    if not query.strip() or not texts or not enabled():
        return None
    try:
        vectors = await asyncio.wait_for(asyncio.to_thread(embed, [query, *texts]), TIMEOUT_S)
    except Exception:
        logger.debug('Memory embedding timed out or failed', exc_info=True)
        return None
    if not vectors:
        return None
    sims = _unit(vectors)
    return [float(x) for x in sims[1:] @ sims[0]]


def _unit(vectors: Sequence[Sequence[float]]):
    """Rows scaled to length 1, so a matrix product gives every cosine at once."""
    import numpy as np

    arr = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


async def similar_pairs(texts: list[str], threshold: float) -> list[tuple[int, int, float]] | None:
    """Index pairs whose meaning is at least ``threshold`` alike, closest first."""
    if len(texts) < 2 or not enabled():
        return []
    try:
        vectors = await asyncio.wait_for(asyncio.to_thread(embed, texts), TIMEOUT_S * 3)
    except Exception:
        return None
    if not vectors:
        return None
    import numpy as np

    # One matrix product instead of n² Python loops: 500 memories took seconds before.
    unit = _unit(vectors)
    sims = unit @ unit.T
    rows, cols = np.nonzero(np.triu(sims >= threshold, k=1))
    pairs = [(int(i), int(j), float(sims[i, j])) for i, j in zip(rows, cols, strict=True)]
    pairs.sort(key=lambda p: -p[2])
    return pairs
