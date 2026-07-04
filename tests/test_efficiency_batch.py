"""Regression tests for the efficiency/robustness sweep fixes.

Covers: secret redaction, screenshot encode cache, incremental LLMResponse
build, sync-tool off-loading, workflow failure propagation, and the memory
per-turn retrieval cache + off-loop store construction.
"""

import threading

import pytest

from cognitrix.common.security import redact_secrets
from cognitrix.utils.llm_response import LLMResponse

# --- security: redact_secrets ---

def test_redact_secrets_nested():
    data = {
        'name': 'a',
        'llm': {'api_key': 'sk-live-123', 'model': 'm'},
        'agents': [{'llm': {'api_key': 'x'}}],
    }
    out = redact_secrets(data)
    assert out['llm']['api_key'] == '***'
    assert out['llm']['model'] == 'm'
    assert out['agents'][0]['llm']['api_key'] == '***'
    assert out['name'] == 'a'
    # Empty secret is left as-is (nothing to hide).
    assert redact_secrets({'api_key': ''})['api_key'] == ''


# --- perf: screenshot encode cache ---

def test_image_to_base64_is_cached():
    from PIL import Image

    from cognitrix.utils import image_to_base64

    img = Image.new('RGB', (4, 4), (255, 0, 0))
    calls = {'n': 0}
    real_save = Image.Image.save

    def counting_save(self, *a, **k):
        calls['n'] += 1
        return real_save(self, *a, **k)

    Image.Image.save = counting_save
    try:
        a = image_to_base64(img)
        b = image_to_base64(img)  # same object -> cached, no re-encode
    finally:
        Image.Image.save = real_save
    assert a == b
    assert calls['n'] == 1


def test_image_cache_evicts_on_gc():
    # A weakly-keyed cache must not survive the image being collected — otherwise
    # a reused id() could return a stale image.
    import gc

    from cognitrix.utils import _IMAGE_B64_CACHE, image_to_base64

    def encode_one():
        from PIL import Image
        img = Image.new('RGB', (2, 2), (0, 255, 0))
        image_to_base64(img)
        return len(_IMAGE_B64_CACHE)

    assert encode_one() >= 1
    gc.collect()
    assert len(_IMAGE_B64_CACHE) == 0  # entry dropped once the image is gone


def test_redact_secrets_case_insensitive_headers():
    out = redact_secrets({'extra_headers': {'Authorization': 'Bearer xyz', 'X-Api-Key': 'k'}})
    assert out['extra_headers']['Authorization'] == '***'
    assert out['extra_headers']['X-Api-Key'] == '***'


# --- perf: incremental LLMResponse build ---

def test_llm_response_builds_full_text_incrementally():
    r = LLMResponse()
    for chunk in ("Hello, ", "world", "!"):
        r.add_chunk(chunk)
    assert r.llm_response == "Hello, world!"
    assert r.result == "Hello, world!"


def test_llm_response_still_parses_json_object():
    r = LLMResponse()
    r.add_chunk('{"result": "done", "scratchpad": "notes"}')
    assert r.result == "done"
    assert r.scratchpad == "notes"


def test_llm_response_incomplete_json_is_not_parsed_mid_stream():
    # The O(1) ends-guard (llm_response.py:_parse_structure) must treat a buffer
    # that opens '{' but hasn't closed as raw text, without crashing.
    r = LLMResponse()
    r.add_chunk('{"result": "do')  # opening brace, no closing brace yet
    assert r.result == '{"result": "do'


def test_llm_response_parses_streamed_json_across_chunks():
    # Stream a JSON object token by token; only the final chunk completes it.
    r = LLMResponse()
    for chunk in ('{"resu', 'lt": "', 'done"', '}'):
        r.add_chunk(chunk)  # must not raise on any intermediate (incomplete) state
    assert r.result == "done"


def test_llm_response_parses_json_with_surrounding_whitespace():
    # Leading/trailing whitespace must still be recognized as complete JSON.
    r = LLMResponse()
    r.add_chunk('\n  {"result": "ok"}  \n')
    assert r.result == "ok"


# --- async: sync tools run off the event loop ---

@pytest.mark.asyncio
async def test_sync_tool_runs_in_worker_thread():
    from cognitrix.tools.tool import tool

    main_thread = threading.get_ident()
    seen = {}

    @tool(category='general')
    def where_am_i():
        """Return the thread it ran on."""
        seen['thread'] = threading.get_ident()
        return "ok"

    result = await where_am_i.run()
    assert result.content == "ok"
    assert seen['thread'] != main_thread  # offloaded, didn't block the loop


# --- memory: per-turn retrieval cache + off-loop construction ---

@pytest.mark.asyncio
async def test_retrieval_cache_reuses_same_query(monkeypatch):
    from cognitrix.memory.hybrid_context import HybridContextManager

    mgr = HybridContextManager.__new__(HybridContextManager)
    mgr._vector_store_disabled = False
    mgr._chroma_store = object()
    mgr._retrieval_cache = None
    mgr.max_long_term = 5

    calls = {'n': 0}

    class FakeStore:
        async def retrieve(self, query, k):
            calls['n'] += 1
            return []

    async def ensure():
        return FakeStore()

    mgr._ensure_long_term = ensure
    mgr._format_memories = lambda mems: "ctx"
    mgr.short_term = type('S', (), {'build_prompt': staticmethod(
        lambda a, s: _async([{'role': 'system', 'content': 'sys'}]))})()

    from types import SimpleNamespace
    agent = SimpleNamespace(formatted_system_prompt=lambda: "sys")
    session = SimpleNamespace(chat=[{'role': 'user', 'type': 'text', 'content': 'same q'}])

    await mgr.build_prompt(agent, session)
    await mgr.build_prompt(agent, session)  # identical query -> cache hit
    assert calls['n'] == 1


def _async(value):
    async def _c():
        return value
    return _c()


@pytest.mark.asyncio
async def test_ensure_long_term_returns_none_when_disabled():
    from cognitrix.memory.hybrid_context import HybridContextManager

    mgr = HybridContextManager.__new__(HybridContextManager)
    mgr._vector_store_disabled = True
    mgr._chroma_store = None
    assert await mgr._ensure_long_term() is None
