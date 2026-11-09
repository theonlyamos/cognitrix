"""Search Knowledge: an agent looks things up in the document collections its
owner gave it (the agent page's Knowledge setting). Added per turn only for
agents that have at least one collection; see ``Session._run_turn``.
"""
from __future__ import annotations

from cognitrix.tools.chat_history import HISTORY_TOOL_CATEGORY
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

MAX_RESULTS = 8
PASSAGE_MAX_CHARS = 1400


@tool(category=HISTORY_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task', 'cli'])
async def search_knowledge(query: str, limit: int = 5, *, parent=None) -> ToolOutcome:
    """Search the document collections you were given for passages about a question. Use it
    before answering anything those documents might cover, and say which document an answer
    came from.
    query: what to look for, in plain words.
    Returns the best passages with their document name."""
    from cognitrix import knowledge

    collection_ids = [str(c) for c in (getattr(parent, 'knowledge', None) or [])]
    owner = str(getattr(parent, 'owner_id', None) or current_execution_context().user_id or '')
    if not collection_ids or not owner:
        return ToolOutcome.failure('no_knowledge', 'This agent has no document collections to search.')
    if not str(query or '').strip():
        return ToolOutcome.failure('invalid_query', 'Say what to look for.')
    try:
        limit = max(1, min(int(limit or 5), MAX_RESULTS))
    except (TypeError, ValueError):
        limit = 5
    try:
        hits = await knowledge.search(owner, collection_ids, query, limit)
    except Exception:
        return ToolOutcome.failure('knowledge_unavailable', 'The document collections could not be searched just now.')
    if not hits:
        return ToolOutcome.success('Nothing in the document collections matches that.')
    lines = []
    for number, hit in enumerate(hits, 1):
        text = hit['text'].strip()
        if len(text) > PASSAGE_MAX_CHARS:
            text = text[:PASSAGE_MAX_CHARS] + '…'
        lines.append(f'[{number}] {hit["name"]} (collection: {hit["collection"]}, part {hit["position"] + 1})\n{text}')
    return ToolOutcome.success('\n\n'.join(lines))
