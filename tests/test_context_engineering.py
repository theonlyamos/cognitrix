"""Context engineering: token budgeting, history shaping, and compaction.

CE0: estimate_tokens + LLM.get_context_window
CE1: system prompt carries no scratchpad/todo boilerplate
CE2: oversized tool results are truncated at ingestion
CE3: turn-aware token-budgeted window (past tool exchanges dropped)
CE4: compaction folds old turns into a summary, never destructively on failure
"""

import pytest

from cognitrix.models import Agent
from cognitrix.providers.base import LLM, LLMManager
from cognitrix.sessions.context import partition_turns, shape_history
from cognitrix.tools.utils import ArtifactRef, EntityRef, ToolOutcome
from cognitrix.utils.llm_response import LLMResponse
from cognitrix.utils.tokens import estimate_tokens


def _llm(**kw):
    return LLM(provider="openai", base_url="http://x", api_key="k", model="m", **kw)


def _user(text):
    return {"role": "User", "type": "text", "content": text}


def _assistant(text):
    return {"role": "assistant", "type": "text", "content": text}


def _tool_exchange(i, payload="result"):
    return [
        {"role": "assistant", "type": "tool_calls", "content": "",
         "tool_calls": [{"name": "t", "arguments": {}, "tool_call_id": str(i)}]},
        {"role": "tool", "tool_call_id": str(i), "content": payload},
    ]


def _timing():
    return {"role": "system", "type": "turn_timing", "content": "Took 1s", "duration": 1.0}


# --- CE0 ---

def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100
    msgs = [_user("a" * 400), _assistant("b" * 400)]
    assert 200 <= estimate_tokens(msgs) <= 220  # content + small per-message overhead


def test_context_window_defaults():
    assert _llm().get_context_window() == 128_000
    assert LLM(provider="google", base_url="http://x", api_key="k", model="m").get_context_window() == 1_000_000
    assert _llm(context_window=5000).get_context_window() == 5000


# --- CE1 ---

def test_system_prompt_has_no_scratchpad_boilerplate():
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
    assert "scratchpad" not in ASSISTANT_SYSTEM_PROMPT.lower()
    assert "<todo>" not in ASSISTANT_SYSTEM_PROMPT.lower()


# --- CE2 ---

def test_oversized_tool_result_is_truncated():
    from cognitrix.agents.base import MAX_TOOL_RESULT_CHARS

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    big = "x" * (MAX_TOOL_RESULT_CHARS * 3)
    msgs = agent.process_prompt({
        "type": "tool_calls_result",
        "result": [{"tool_call_id": "1", "data": big}],
    })
    content = msgs[0]["content"]
    assert len(content) < MAX_TOOL_RESULT_CHARS + 200
    assert "[truncated" in content


# --- CE3 ---

def test_past_tool_exchanges_are_dropped_from_prompt():
    chat = (
        [_user("q1")] + _tool_exchange(1) + [_assistant("a1"), _timing()]
        + [_user("q2")] + _tool_exchange(2) + [_assistant("a2"), _timing()]
        + [_user("q3")] + _tool_exchange(3)
    )
    shaped = shape_history(chat, budget_tokens=100_000)
    # Past turns keep only dialogue; current turn keeps its tool exchange.
    roles = [(m["role"], m.get("type")) for m in shaped]
    assert ("User", "text") == roles[0]
    assert all(m.get("type") != "turn_timing" for m in shaped)
    past = shaped[: roles.index(("User", "text")) + 1]
    tool_msgs = [m for m in shaped if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["tool_call_id"] == "3"
    assert {"a1", "a2"} <= {m.get("content") for m in shaped}
    assert past is not None


def test_budget_limits_past_turns():
    chat = []
    for i in range(10):
        chat += [_user(f"question {i} " + "x" * 4000), _assistant(f"answer {i} " + "y" * 4000)]
    chat += [_user("current question")]
    # ~2000 tokens per past turn; a 5000-token budget fits at most 2 past turns.
    shaped = shape_history(chat, budget_tokens=5000)
    users = [m["content"] for m in shaped if m["role"] == "User"]
    assert users[-1] == "current question"
    assert len(users) <= 3
    # Newest past turns win.
    assert any("question 9" in u for u in users)


def test_summary_message_is_kept_and_formatted():
    chat = [
        {"role": "user", "type": "summary", "content": "[Summary of the earlier conversation]\nfacts"},
        _user("q"), _assistant("a"),
        _user("current"),
    ]
    shaped = shape_history(chat, budget_tokens=100_000)
    assert shaped[0]["type"] == "summary"
    formatted = LLMManager.format_query(_llm(), shaped)
    assert formatted[0]["role"] == "user"
    assert "facts" in formatted[0]["content"]


def test_current_turn_kept_even_over_budget():
    chat = [_user("q " + "z" * 8000)] + _tool_exchange(1, payload="r" * 8000)
    shaped = shape_history(chat, budget_tokens=100)
    assert len(shaped) == 3  # never drop the current turn


def test_past_images_are_safe_text_placeholders():
    chat = [
        {"role": "User", "type": "image", "content": "data:image/png;base64,secret", "artifact": {"id": "old"}},
        _assistant("done"),
        _user("next"),
    ]
    shaped = shape_history(chat, budget_tokens=100_000)
    assert shaped[0] == {"role": "User", "type": "text", "content": "[Previously supplied image: old]"}


def test_tool_outcome_model_content_omits_storage_and_ownership():
    from cognitrix.agents.base import _tool_result_entry

    outcome = ToolOutcome.success(
        "Image generated.",
        artifacts=[ArtifactRef(id="image-1", mime_type="image/png", filename="result.png")],
        entities=[EntityRef(type="image", id="image-1", name="Result")],
        warnings=["One warning"],
    )
    content = outcome.model_content()
    assert "Image generated." in content
    assert "Artifact: image-1 image/png result.png" in content
    assert "Entity: image image-1 Result" in content
    assert "storage" not in content.lower()
    assert _tool_result_entry("call-1", outcome)["data"] == content


@pytest.mark.asyncio
async def test_context_managers_keep_media_system_message(monkeypatch):
    from cognitrix.memory.hybrid_context import HybridContextManager
    from cognitrix.sessions.base import Session
    from cognitrix.sessions.context import SlidingWindowContextManager

    async def enrich(self, session, history):
        return {"role": "system", "type": "media_context", "content": "media rules"}, history

    monkeypatch.setattr("cognitrix.media.context.MediaContextBuilder.enrich", enrich)
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    session = Session(agent_id="media-context")
    sliding = await SlidingWindowContextManager().build_prompt(agent, session)
    hybrid = await HybridContextManager("media-context").build_prompt(agent, session)
    assert sliding[1]["type"] == "media_context"
    assert hybrid[1]["type"] == "media_context"


# --- CE4 ---

@pytest.mark.asyncio
async def test_compaction_folds_old_turns(monkeypatch):
    from cognitrix.sessions.base import COMPACT_KEEP_TURNS, Session

    llm = _llm(context_window=3000)  # budget = max(2000, 3000-4096-2000) = 2000
    agent = Agent(name="A", llm=llm, system_prompt="sys")
    session = Session(agent_id="c1")
    for i in range(10):
        session.chat += [_user(f"q{i} " + "x" * 2000), _assistant(f"a{i} " + "y" * 2000)]

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        r = LLMResponse()
        r.add_chunk("the compressed summary")
        return r

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)

    await session._maybe_compact(agent)

    assert session.chat[0]["type"] == "summary"
    assert "the compressed summary" in session.chat[0]["content"]
    # summary head + the kept turns (2 messages each)
    assert len(partition_turns(session.chat)) == COMPACT_KEEP_TURNS + 1


@pytest.mark.asyncio
async def test_compaction_failure_keeps_history(monkeypatch):
    from cognitrix.sessions.base import Session

    llm = _llm(context_window=3000)
    agent = Agent(name="A", llm=llm, system_prompt="sys")
    session = Session(agent_id="c2")
    for i in range(10):
        session.chat += [_user(f"q{i} " + "x" * 2000), _assistant(f"a{i} " + "y" * 2000)]
    before = list(session.chat)

    async def err_generate(llm, prompt, stream=False, tools=None, **kw):
        return LLMResponse(llm_response="Error: boom", error="Error: boom")

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(err_generate)
    )

    async def fake_save(self):
        raise AssertionError("must not save on failed compaction")

    monkeypatch.setattr(Session, "save", fake_save)

    await session._maybe_compact(agent)
    assert session.chat == before


@pytest.mark.asyncio
async def test_compaction_noop_under_threshold(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")  # 128k window
    session = Session(agent_id="c3")
    session.chat = [_user("hi"), _assistant("hello")]
    before = list(session.chat)

    async def boom(llm, prompt, stream=False, tools=None, **kw):
        raise AssertionError("summarizer must not be called under threshold")

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(boom)
    )
    await session._maybe_compact(agent)
    assert session.chat == before
