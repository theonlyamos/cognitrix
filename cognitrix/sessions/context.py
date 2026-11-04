from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from cognitrix.utils.tokens import estimate_tokens

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.sessions.base import Session

class BaseContextManager(ABC):
    """Abstract base class for context managers."""

    @abstractmethod
    async def build_prompt(self, agent: 'Agent', session: 'Session') -> list[dict[str, Any]]:
        """
        Builds a context-aware prompt for the LLM.

        Args:
            agent: The agent that will process the prompt.
            session: The current session containing the chat history.

        Returns:
            A list of dictionaries formatted for the LLM provider.
        """
        pass


def partition_turns(chat: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split chat into turns; each turn starts at a user text/summary message.

    User image and image-selection records are companions to the preceding
    request, not independent turns. Messages before the first user request
    form their own leading group.
    """
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for m in chat:
        is_user_request = (
            str(m.get('role', '')).lower() == 'user'
            and m.get('type', 'text') in {'text', 'summary'}
        )
        if is_user_request and current:
            turns.append(current)
            current = []
        current.append(m)
    if current:
        turns.append(current)
    return turns


def _slim_past_turn(turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Past turns only need the dialogue: user text/summary + assistant text.

    Tool exchanges and timing entries are dead weight once the turn is over —
    dropping them keeps the window protocol-valid by construction (it always
    starts at a user message, never mid tool-exchange).
    """
    kept = []
    for m in turn:
        role = str(m.get('role', '')).lower()
        mtype = m.get('type', 'text')
        if role == 'user' and mtype in ('image', 'image_selection'):
            artifact = m.get('artifact') or {}
            artifact_id = str(artifact.get('id') or 'unknown')
            kept.append({
                'role': m.get('role', 'User'),
                'type': 'text',
                'content': f'[Previously supplied image: {artifact_id}]',
            })
        elif role == 'user' and mtype in ('text', 'summary'):
            kept.append(m)
        elif role == 'assistant' and mtype == 'text' and not m.get('tool_calls'):
            kept.append(m)
    return kept


# The newest past turns keep their tool exchanges (results truncated). Without
# them the model sees answers it can't account for, and stricter models then
# retract correct answers as unsupported. Older turns are slimmed as before.
RECENT_TOOL_TURNS = 3
PAST_TOOL_RESULT_CHARS = 2000


def _compact_past_turn(turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A past turn with its tool exchange kept and tool results truncated.

    Falls back to the slim form when any tool call lacks a result, so the
    window always stays protocol-valid.
    """
    call_ids = {
        str(tc.get('tool_call_id'))
        for m in turn for tc in (m.get('tool_calls') or []) if isinstance(tc, dict)
    }
    if not call_ids:
        return _slim_past_turn(turn)
    result_ids = {
        str(m.get('tool_call_id')) for m in turn
        if str(m.get('role', '')).lower() == 'tool'
    }
    if not call_ids <= result_ids:
        return _slim_past_turn(turn)

    kept = []
    for m in turn:
        role = str(m.get('role', '')).lower()
        if m.get('type') == 'turn_timing':
            continue
        if role == 'tool':
            if str(m.get('tool_call_id')) not in call_ids:
                continue
            content = m.get('content')
            if isinstance(content, str) and len(content) > PAST_TOOL_RESULT_CHARS:
                m = {**m, 'content': content[:PAST_TOOL_RESULT_CHARS]
                     + f"\n[... truncated {len(content) - PAST_TOOL_RESULT_CHARS} chars]"}
            kept.append(m)
        elif role == 'assistant' and m.get('tool_calls'):
            kept.append(m)
        elif m.get('type') == 'progress_summary':
            # Stands in for the folded tool rounds, which are gone from the
            # prompt; dropping it would lose what those rounds found.
            kept.append(m)
        else:
            kept.extend(_slim_past_turn([m]))
    return kept


# A single long turn (dozens of tool calls) can outgrow the model's window on
# its own. When it does, the oldest tool results in that turn are shortened;
# the newest ones stay whole. Results are cut in blocks so the prompt prefix
# stays the same for several rounds (provider prompt caches keep working).
KEEP_RECENT_RESULTS = 8
SHORTENED_RESULT_CHARS = 400
SHORTEN_BLOCK = 8
# After shortening, aim this far under the budget so the next rounds fit too.
SHORTEN_TARGET = 0.75


def _shorten(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get('content')
    if not isinstance(content, str) or len(content) <= SHORTENED_RESULT_CHARS:
        return message
    return {**message, 'content': (
        content[:SHORTENED_RESULT_CHARS]
        + f"\n[Older tool result shortened to save room: {len(content)} chars in full. "
        "You can no longer see the rest; run the tool again if you need it.]"
    )}


def fit_current_turn(turn: list[dict[str, Any]], budget_tokens: int) -> list[dict[str, Any]]:
    """Shorten the oldest tool results of a turn that is over the budget.

    Messages are copied, never changed in place, so the stored history keeps
    the full results. The tool-call protocol stays valid: every result is
    kept, only its text gets shorter.
    """
    if estimate_tokens(turn) <= budget_tokens:
        return turn
    tool_positions = [
        i for i, m in enumerate(turn)
        if str(m.get('role', '')).lower() == 'tool'
        and isinstance(m.get('content'), str)
        and len(m['content']) > SHORTENED_RESULT_CHARS
    ]
    candidates = tool_positions[:-KEEP_RECENT_RESULTS] if KEEP_RECENT_RESULTS else tool_positions
    if not candidates:
        return turn
    target = budget_tokens * SHORTEN_TARGET
    used = estimate_tokens(turn)
    count = 0
    for i in candidates:
        if used <= target:
            break
        used -= estimate_tokens(turn[i]) - estimate_tokens(_shorten(turn[i]))
        count += 1
    # Round up to a whole block, so the cut point moves rarely.
    count = min(len(candidates), -(-count // SHORTEN_BLOCK) * SHORTEN_BLOCK)
    cut = set(candidates[:count])
    return [_shorten(m) if i in cut else m for i, m in enumerate(turn)]


def shape_history(
    chat: list[dict[str, Any]],
    budget_tokens: int,
    max_past_turns: int = 20,
) -> list[dict[str, Any]]:
    """Select history for the prompt under a token budget.

    The current (last) turn is kept whole — the tool-call protocol needs its
    assistant tool_calls + tool results — except that its oldest tool results
    are shortened when the turn alone is over the budget. The newest past turns keep their tool
    exchanges with truncated results; older ones are slimmed to plain
    dialogue. Turns are added newest-first until the budget or turn cap is hit.
    """
    # Messages folded into a progress summary by mid-turn compaction stay in
    # the stored chat (the UI shows them) but never go back to the model.
    chat = [m for m in chat if not m.get('folded')]
    turns = partition_turns(chat)
    if not turns:
        return []

    selected = [m for m in turns[-1] if m.get('type') != 'turn_timing']
    selected = fit_current_turn(selected, budget_tokens)
    used = estimate_tokens(selected)

    past_added = 0
    for turn in reversed(turns[:-1]):
        if past_added < RECENT_TOOL_TURNS:
            slim = _compact_past_turn(turn)
        else:
            slim = _slim_past_turn(turn)
        if not slim:
            continue
        cost = estimate_tokens(slim)
        if used + cost > budget_tokens and past_added < RECENT_TOOL_TURNS:
            slim = _slim_past_turn(turn)
            cost = estimate_tokens(slim)
        if used + cost > budget_tokens or past_added >= max_past_turns:
            break
        selected = slim + selected
        used += cost
        past_added += 1
    return selected


def _trim_to_valid_start(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Safety net for histories that don't start at a user message.

    shape_history produces user-anchored windows by construction; this only
    matters for legacy/degenerate histories: drop leading tool/tool_calls
    messages so the window can't begin mid tool-exchange.
    """
    for i, m in enumerate(messages):
        if str(m.get('role', '')).lower() == 'user':
            return messages[i:]
    i = 0
    while i < len(messages) and (
        str(messages[i].get('role', '')).lower() == 'tool' or messages[i].get('tool_calls')
    ):
        i += 1
    return messages[i:]


class SlidingWindowContextManager(BaseContextManager):
    """
    Token-budgeted, turn-aware context window.

    `max_messages` is kept for backward compatibility and acts as a cap on the
    number of past turns included (the current turn is always whole).
    """
    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages

    async def build_prompt(self, agent: 'Agent', session: 'Session') -> list[dict[str, Any]]:
        """System prompt + as much recent dialogue as fits the token budget."""
        system_content = agent.formatted_system_prompt()
        system_prompt = {
            'role': 'system',
            'content': system_content
        }

        llm = agent.llm
        # Reserve room for the model's output plus a safety margin.
        # The 4-chars-per-token estimate runs low on some tokenizers (Claude
        # counted ~10% more on long tool output in testing) and tool schemas
        # aren't counted, so only 85% of the room is planned for.
        budget = max(
            2000,
            int((llm.get_context_window() - estimate_tokens(system_content) - llm.max_tokens - 1000) * 0.85),
        )

        recent_history = shape_history(session.chat, budget, max_past_turns=self.max_messages)
        recent_history = _trim_to_valid_start(recent_history)

        from cognitrix.media.context import MediaContextBuilder

        media_message, recent_history = await MediaContextBuilder().enrich(
            session, recent_history
        )
        prompt = [system_prompt]
        if media_message is not None:
            prompt.append(media_message)
        prompt.extend(recent_history)
        return prompt
