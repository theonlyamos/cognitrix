"""Token estimation for context budgeting.

ponytail: chars//4 heuristic — good enough to budget a prompt window and
trigger compaction. Swap in a real tokenizer only if the estimate proves
too loose in practice; real usage numbers come from the provider anyway
(LLMResponse.usage).
"""

from typing import Any

CHARS_PER_TOKEN = 4


def estimate_tokens(content: str | dict[str, Any] | list[Any] | None) -> int:
    """Rough token count for a string, a message dict, or a message list."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content) // CHARS_PER_TOKEN
    if isinstance(content, dict):
        total = estimate_tokens(str(content.get('content') or ''))
        for tc in content.get('tool_calls') or []:
            total += estimate_tokens(str(tc))
        return total + 4  # per-message overhead
    return sum(estimate_tokens(m) for m in content)
