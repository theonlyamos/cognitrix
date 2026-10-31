"""Shared embedding model utilities.

Embeddings come from ChromaDB's built-in all-MiniLM-L6-v2 model, which runs on
onnxruntime. This avoids pulling in sentence-transformers and PyTorch (hundreds
of MB to GBs) for the same model. The ONNX weights (~80 MB) are downloaded to
ChromaDB's cache on first use.
"""

import logging
import threading
from typing import Any

logger = logging.getLogger('cognitrix.log')

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class EmbeddingModel:
    """Small wrapper exposing ``encode(text)`` over a ChromaDB embedding function."""

    def __init__(self, embedding_function: Any, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self._fn = embedding_function
        self.model_name = model_name

    def encode(self, text: str):
        """Return the embedding for one string as a numpy array."""
        import numpy as np

        return np.asarray(self._fn([text])[0], dtype=np.float32)


_embedding_model_cache: EmbeddingModel | None = None
_cache_lock = threading.Lock()


def get_embedding_model(model_name: str = DEFAULT_EMBEDDING_MODEL) -> EmbeddingModel:
    """Get or create the shared embedding model.

    Only all-MiniLM-L6-v2 is bundled; other names fall back to it with a warning.
    """
    global _embedding_model_cache

    if _embedding_model_cache is None:
        with _cache_lock:
            if _embedding_model_cache is None:
                if model_name != DEFAULT_EMBEDDING_MODEL:
                    logger.warning(
                        f"Embedding model '{model_name}' is not available; "
                        f"using {DEFAULT_EMBEDDING_MODEL}"
                    )
                logger.info(f"Loading embedding model: {DEFAULT_EMBEDDING_MODEL}")
                # Hold one ONNXMiniLM_L6_V2 instance: it caches its tokenizer and
                # inference session. DefaultEmbeddingFunction builds a new one
                # on every call, which would reload the model each time.
                from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

                _embedding_model_cache = EmbeddingModel(ONNXMiniLM_L6_V2())
                logger.info(f"Embedding model loaded: {DEFAULT_EMBEDDING_MODEL}")

    return _embedding_model_cache


def clear_embedding_model_cache():
    """Clear the cached embedding model."""
    global _embedding_model_cache
    _embedding_model_cache = None
    logger.info("Embedding model cache cleared")
