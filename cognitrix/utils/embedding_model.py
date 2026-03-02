"""Shared embedding model utilities."""

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger('cognitrix.log')

_embedding_model_cache: Optional['SentenceTransformer'] = None


def get_embedding_model(model_name: str = "all-MiniLM-L6-v2"):
    """
    Get or create singleton embedding model.
    
    Uses module-level caching to ensure only one instance
    of the embedding model is loaded across all components.
    
    Args:
        model_name: Name of the sentence transformer model
        
    Returns:
        SentenceTransformer instance
    """
    global _embedding_model_cache
    
    if _embedding_model_cache is None:
        logger.info(f"Loading embedding model: {model_name}")
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model_cache = SentenceTransformer(model_name)
            logger.info(f"Embedding model loaded: {model_name}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise
    
    return _embedding_model_cache


def clear_embedding_model_cache():
    """Clear the cached embedding model."""
    global _embedding_model_cache
    _embedding_model_cache = None
    logger.info("Embedding model cache cleared")
