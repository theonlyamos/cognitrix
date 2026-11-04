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


def test_system_prompt_requires_explicit_persisted_task_request():
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT

    prompt = ASSISTANT_SYSTEM_PROMPT.lower()
    assert "complete requests directly with ordinary tools" in prompt
    assert "create a persisted task only when the user explicitly asks" in prompt


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

def test_recent_past_turns_keep_tool_exchanges_and_older_ones_drop_them():
    chat = []
    for i in range(1, 6):
        chat += [_user(f"q{i}")] + _tool_exchange(i, payload="r" * 5000) + [_assistant(f"a{i}"), _timing()]
    chat += [_user("q6")] + _tool_exchange(6)
    shaped = shape_history(chat, budget_tokens=100_000)
    assert ("User", "text") == (shaped[0]["role"], shaped[0].get("type"))
    assert all(m.get("type") != "turn_timing" for m in shaped)
    tool_ids = [m["tool_call_id"] for m in shaped if m["role"] == "tool"]
    # Current turn plus the 3 newest past turns keep their tool exchanges.
    assert tool_ids == ["3", "4", "5", "6"]
    # Past tool results are truncated; the current turn's is untouched.
    past_results = [m["content"] for m in shaped if m["role"] == "tool" and m["tool_call_id"] != "6"]
    assert all(len(r) < 2100 and "[... truncated" in r for r in past_results)
    assert {"a1", "a2", "a3", "a4", "a5"} <= {m.get("content") for m in shaped}
    # Every kept tool call has its result.
    calls = {tc["tool_call_id"] for m in shaped for tc in (m.get("tool_calls") or [])}
    assert calls == set(tool_ids)


def test_past_turn_with_unanswered_tool_call_is_slimmed():
    chat = [
        _user("q1"),
        {"role": "assistant", "type": "tool_calls", "content": "",
         "tool_calls": [{"name": "t", "arguments": {}, "tool_call_id": "x"}]},
        _assistant("a1"),
        _user("q2"),
    ]
    shaped = shape_history(chat, budget_tokens=100_000)
    assert not any(m.get("tool_calls") for m in shaped)
    assert [m["content"] for m in shaped] == ["q1", "a1", "q2"]


def test_past_turn_without_tools_is_unchanged():
    shaped = shape_history([_user("q1"), _assistant("a1"), _user("q2")], budget_tokens=100_000)
    assert shaped[1]["content"] == "a1"


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


def test_media_context_is_formatted_as_a_system_message():
    formatted = LLMManager.format_query(_llm(), [{
        "role": "system",
        "type": "media_context",
        "content": "Selected edit source: image-1",
    }])
    assert formatted == [{
        "role": "system",
        "content": "Selected edit source: image-1",
    }]


def test_current_turn_kept_even_over_budget():
    chat = [_user("q " + "z" * 8000)] + _tool_exchange(1, payload="r" * 8000)
    shaped = shape_history(chat, budget_tokens=100)
    assert len(shaped) == 3  # never drop the current turn


def test_media_companions_stay_in_their_user_turn():
    chat = [
        _user("edit these"),
        {"role": "User", "type": "image", "content": "pixels", "artifact": {"id": "a"}},
        {
            "role": "User", "type": "image_selection", "content": "selected",
            "artifact": {"id": "a"},
        },
        *_tool_exchange(1),
    ]
    turns = partition_turns(chat)
    assert turns == [chat]


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


@pytest.mark.parametrize("text", [
    'What is "city" in this JSON? {"city": "Accra", "temp": 31}',
    'Step 2\n\nReviewer feedback:\n{"reviewer_suggestions": ["fix"], "previous_attempt": "x"}',
    '{"city": "Accra"}',
])
def test_user_text_containing_json_is_kept_verbatim(text):
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    msg = agent.process_prompt(text)
    assert msg["content"] == text


def test_structured_result_string_is_still_unpacked():
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    msg = agent.process_prompt('{"result": "done"}')
    assert msg["content"] == "done"


# --- CE5: one long turn that outgrows the window --------------------------

def _long_turn(rounds, size=8000):
    turn = [_user("read all the files")]
    for i in range(rounds):
        turn += _tool_exchange(i, payload=f"file {i} " + "x" * size)
    return turn


def test_long_current_turn_is_shortened_to_fit():
    from cognitrix.sessions.context import KEEP_RECENT_RESULTS

    chat = _long_turn(60)  # ~120k tokens in one turn
    budget = 40_000
    out = shape_history(chat, budget)
    assert estimate_tokens(out) <= budget
    # Every call still has its result (protocol stays valid).
    assert [m.get("tool_call_id") for m in out if m["role"] == "tool"] == [str(i) for i in range(60)]
    tools = [m for m in out if m["role"] == "tool"]
    # Newest results are whole, oldest are shortened with a note.
    assert all(len(m["content"]) > 8000 for m in tools[-KEEP_RECENT_RESULTS:])
    assert "shortened to save room" in tools[0]["content"]
    assert tools[0]["content"].startswith("file 0 ")
    # Stored history is untouched.
    assert len(chat[2]["content"]) > 8000


def test_short_current_turn_is_left_alone():
    chat = _long_turn(3)
    assert shape_history(chat, 100_000) == chat


def test_shortening_moves_in_blocks():
    """The cut point stays put for a few rounds, so prompt caches still hit."""
    budget = 40_000
    cuts = []
    for rounds in range(40, 48):
        out = shape_history(_long_turn(rounds), budget)
        cuts.append(sum("shortened to save room" in m["content"] for m in out if m["role"] == "tool"))
    assert len(set(cuts)) < len(cuts)


# --- CE6: compaction inside a running turn ---------------------------------

def _patch_llm(monkeypatch, text="found: file 0..13 values", seen=None):
    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        if seen is not None:
            seen.append(prompt)
        r = LLMResponse()
        if text is None:
            return LLMResponse(llm_response="Error: boom", error="Error: boom")
        r.add_chunk(text)
        return r

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )


@pytest.mark.asyncio
async def test_turn_compaction_folds_older_rounds(monkeypatch):
    from cognitrix.sessions.base import TURN_KEEP_ROUNDS, Session

    seen = []
    _patch_llm(monkeypatch, seen=seen)
    agent = Agent(name="A", llm=_llm(context_window=40_000, max_tokens=4000), system_prompt="sys")
    session = Session(agent_id="t1")
    session.chat = [_user("earlier q"), _assistant("earlier a"), *_long_turn(18)]
    total = len(session.chat)

    assert await session._maybe_compact_turn(agent) is True
    # Nothing deleted: one summary added, older rounds marked folded.
    assert len(session.chat) == total + 1
    folded = [m for m in session.chat if m.get("folded")]
    assert len(folded) == 2 * (18 - TURN_KEEP_ROUNDS)
    # The summarizer saw the request and the folded results.
    assert "read all the files" in seen[0][1]["content"]
    assert "file 0 " in seen[0][1]["content"]

    shaped = shape_history(session.chat, 30_000)
    assert not any(m.get("folded") for m in shaped)
    summary = [m for m in shaped if m.get("type") == "progress_summary"]
    assert len(summary) == 1 and "found: file 0..13 values" in summary[0]["content"]
    # The request and the newest rounds are still there, in order, protocol-valid.
    assert shaped[0]["content"] == "earlier q"
    assert any(m.get("content") == "read all the files" for m in shaped)
    ids = [m["tool_call_id"] for m in shaped if m["role"] == "tool"]
    assert ids == [str(i) for i in range(18 - TURN_KEEP_ROUNDS, 18)]
    out = LLMManager.format_query(agent.llm, shaped)
    assert any("found: file 0..13 values" in str(m.get("content")) for m in out)


@pytest.mark.asyncio
async def test_turn_compaction_runs_again_and_merges(monkeypatch):
    from cognitrix.sessions.base import Session

    seen = []
    _patch_llm(monkeypatch, seen=seen)
    agent = Agent(name="A", llm=_llm(context_window=40_000, max_tokens=4000), system_prompt="sys")
    session = Session(agent_id="t2")
    session.chat = _long_turn(18)
    assert await session._maybe_compact_turn(agent)
    session.chat += [m for m in _long_turn(30)[1 + 2 * 18:]]  # rounds 18..29
    assert await session._maybe_compact_turn(agent)
    # The second summary carries the first one forward.
    assert "Progress summary so far" in seen[-1][1]["content"]
    live = [m for m in session.chat if not m.get("folded") and m.get("type") == "progress_summary"]
    assert len(live) == 1


@pytest.mark.asyncio
async def test_turn_compaction_noop_when_small_or_failed(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(context_window=40_000, max_tokens=4000), system_prompt="sys")
    _patch_llm(monkeypatch)
    small = Session(agent_id="t3")
    small.chat = _long_turn(3)
    assert await small._maybe_compact_turn(agent) is False

    _patch_llm(monkeypatch, text=None)
    big = Session(agent_id="t4")
    big.chat = _long_turn(18)
    before = list(big.chat)
    assert await big._maybe_compact_turn(agent) is False
    assert big.chat == before


@pytest.mark.asyncio
async def test_turn_compaction_summarizer_sees_middle_of_results(monkeypatch):
    """A value in the middle of a long result must reach the summarizer."""
    from cognitrix.sessions.base import Session

    seen = []
    _patch_llm(monkeypatch, seen=seen)
    agent = Agent(name="A", llm=_llm(context_window=40_000, max_tokens=4000), system_prompt="sys")
    session = Session(agent_id="t5")
    turn = [_user("collect the letters")]
    for i in range(18):
        turn += _tool_exchange(i, payload="a" * 3500 + f" LETTER-{i} " + "b" * 3500)
    session.chat = turn
    assert await session._maybe_compact_turn(agent)
    sent = "\n".join(p[1]["content"] for p in seen)
    for i in range(18 - 4):
        assert f"LETTER-{i} " in sent
    assert len(seen) > 1  # too big for one summarizer call: done in pieces
    assert "Progress summary so far" in seen[-1][1]["content"]
