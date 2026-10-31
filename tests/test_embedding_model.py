"""Tests for the shared embedding model wrapper."""

from unittest.mock import patch

import numpy as np

from cognitrix.utils import embedding_model as em


def test_encode_returns_float32_vector():
    model = em.EmbeddingModel(lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    vec = model.encode("hello")
    assert isinstance(vec, np.ndarray)
    assert vec.dtype == np.float32
    assert vec.tolist() == np.asarray([0.1, 0.2, 0.3], dtype=np.float32).tolist()


def test_get_embedding_model_is_cached_and_uses_chroma_onnx():
    em.clear_embedding_model_cache()
    with patch(
        "chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2",
        return_value=lambda texts: [[1.0] for _ in texts],
    ) as factory:
        first = em.get_embedding_model()
        second = em.get_embedding_model("some-other-model")
    assert first is second
    assert factory.call_count == 1
    assert first.encode("x").tolist() == [1.0]
    em.clear_embedding_model_cache()
