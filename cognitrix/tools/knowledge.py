"""Search Knowledge: an agent looks things up in the document collections its
owner gave it (the agent page's Knowledge setting). Learn From Website: the
agent adds a web page (or a site section) to one of those collections. Both
are added per turn only for agents that have at least one collection; see
``Session._run_turn``.
"""
from __future__ import annotations

import asyncio

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
        where = f'page: {hit["url"]}' if hit.get('url') else f'part {hit["position"] + 1}'
        lines.append(f'[{number}] {hit["name"]} (collection: {hit["collection"]}, {where})\n{text}')
    return ToolOutcome.success('\n\n'.join(lines))


LEARN_WAIT_SECONDS = 90


@tool(category=HISTORY_TOOL_CATEGORY, retryable=False, max_attempts=1,
      supported_interfaces=['web', 'ws', 'task', 'cli'])
async def learn_from_website(url: str, collection: str = '', max_pages: int = 1, *, parent=None) -> ToolOutcome:
    """Add a web page to one of your document collections so you (and the person's other
    agents with that collection) can search it later with Search Knowledge. Use it when the
    person asks you to learn, remember or keep a website or its docs, not for a one-off look
    (use a web fetch for that).
    url: the page's address.
    collection: the collection's name; leave empty when you have only one.
    max_pages: 1 reads just this page; up to 50 also reads the pages it links to under the
    same folder of the same site (a docs section, for example).
    Returns what was read."""
    from cognitrix import knowledge
    from cognitrix.knowledge.models import KnowledgeDocument

    collection_ids = [str(c) for c in (getattr(parent, 'knowledge', None) or [])]
    owner = str(getattr(parent, 'owner_id', None) or current_execution_context().user_id or '')
    if not collection_ids or not owner:
        return ToolOutcome.failure('no_knowledge', 'This agent has no document collections to add pages to.')
    mine = [row for row in await knowledge.collections(owner) if str(row.id) in collection_ids]
    if not mine:
        return ToolOutcome.failure('no_knowledge', 'This agent has no document collections to add pages to.')
    wanted = ' '.join(str(collection or '').split()).lower()
    if wanted:
        chosen = next((row for row in mine if row.name.lower() == wanted or str(row.id) == wanted), None)
        if chosen is None:
            names = ', '.join(row.name for row in mine)
            return ToolOutcome.failure('unknown_collection', f'No collection called {collection!r}. Yours: {names}.')
    elif len(mine) == 1:
        chosen = mine[0]
    else:
        names = ', '.join(row.name for row in mine)
        return ToolOutcome.failure('collection_required', f'Say which collection to add it to: {names}.')
    try:
        pages = max(1, min(int(max_pages or 1), 50))
    except (TypeError, ValueError):
        pages = 1
    try:
        row = await knowledge.add_url(chosen, url, max_pages=pages)
    except knowledge.KnowledgeError as exc:
        return ToolOutcome.failure('cannot_add', str(exc))
    # Reading happens in the background; wait a while so the pages can be
    # searched straight away, and say so when it takes longer.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LEARN_WAIT_SECONDS
    while loop.time() < deadline:
        current = await KnowledgeDocument.get(row.id)
        if current is None:
            return ToolOutcome.failure('removed', 'The page was removed while it was being read.')
        if current.status == 'failed':
            return ToolOutcome.failure('read_failed', f'Could not read {url}: {current.error or "unknown error"}')
        if current.status == 'ready':
            extra = f' ({current.error})' if current.error else ''
            return ToolOutcome.success(
                f'Added "{current.name}" to {chosen.name}: {current.pages_read} page'
                f'{"s" if current.pages_read != 1 else ""}, {current.passages} passages{extra}. '
                'Search it with Search Knowledge.')
        await asyncio.sleep(1.0)
    return ToolOutcome.success(
        f'Still reading {url} into {chosen.name}. It shows on the Knowledge page and can be searched once done.')
