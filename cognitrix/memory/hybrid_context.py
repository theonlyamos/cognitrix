"""Hybrid context manager combining short-term and long-term memory."""

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from cognitrix.memory.chroma_store import ChromaMemoryStore
from cognitrix.sessions.context import SlidingWindowContextManager

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

INCOGNITO_NOTE = (
    "## Incognito chat\n"
    "The person started this chat in incognito. You can't see their saved memories, and "
    "nothing from this chat is remembered afterwards. If they ask you to remember something, "
    "say it won't be kept because the chat is incognito. Don't write it to a file, notes or "
    "any other place instead."
)


class ImportanceScorer:
    """Scores the importance of a message for memory storage."""

    HIGH_IMPORTANCE_KEYWORDS = [
        'error', 'exception', 'failed', 'success', 'completed',
        'important', 'critical', 'key', 'result', 'conclusion',
        'remember', 'note', 'save', 'permanent'
    ]

    # Things people say about themselves or ask to be kept: "my name is",
    # "I prefer", "call me", "remember that". Plain facts like these scored
    # 0.3-0.4 before and were never kept.
    PERSONAL_FACT_PATTERNS = re.compile(
        r"\b(remember|don't forget|do not forget|keep in mind|note that|"
        r"my (?:name|codename|email|role|job|team|project|preference|favou?rite)|"
        r"call me|i am|i'm|i prefer|i like|i use|i work|i live|we use|our )",
        re.IGNORECASE,
    )

    def __init__(self):
        self.min_importance = 0.3  # Always remember something
        self.max_importance = 1.0

    def score(self, message: dict[str, Any]) -> float:
        """
        Score message importance (0.0 - 1.0).

        Higher scores = more likely to be stored in long-term memory.
        """
        content = message.get('content', '').lower()
        role = message.get('role', '').lower()

        score = self.min_importance

        if role == 'user' and self.PERSONAL_FACT_PATTERNS.search(content):
            score += 0.4

        # Boost for important keywords
        for keyword in self.HIGH_IMPORTANCE_KEYWORDS:
            if keyword in content:
                score += 0.1

        # Boost for system/assistant messages (usually more informative)
        if role in ['system', 'assistant']:
            score += 0.1

        # Boost for longer, more detailed messages
        word_count = len(content.split())
        if word_count > 50:
            score += 0.1
        if word_count > 200:
            score += 0.1

        # Boost for messages with structured data (JSON, code)
        if any(char in content for char in ['{', '}', '[', ']', '```']):
            score += 0.1

        return min(score, self.max_importance)


from cognitrix.sessions.context import BaseContextManager


class HybridContextManager(BaseContextManager):
    """
    Combines sliding window short-term memory with vector long-term memory.

    Uses ChromaDB for persistent semantic search and sliding window for
    recent conversation context.
    """

    def __init__(
        self,
        agent_id: str,
        max_short_term: int = 10,
        max_long_term: int = 5,
        importance_threshold: float = 0.7
    ):
        """
        Initialize hybrid context manager.

        Args:
            agent_id: Unique ID for agent's memory collection
            max_short_term: Number of recent messages to keep in context
            max_long_term: Number of relevant memories to retrieve
            importance_threshold: Minimum importance to store in long-term
        """
        self.agent_id = agent_id
        self.short_term = SlidingWindowContextManager(max_short_term)
        # Lazy load ChromaDB - uses default persist directory from ChromaMemoryStore
        self._chroma_config = {
            'collection_name': f"agent_{agent_id}"
        }
        self._chroma_store = None
        self._long_term_lock = asyncio.Lock()
        # 1-entry cache of the last retrieval (query -> formatted memory context)
        # so repeated tool rounds in a turn don't re-embed the same query.
        self._retrieval_cache: tuple[str, str] | None = None
        self.importance_scorer = ImportanceScorer()
        self.importance_threshold = importance_threshold
        self.max_long_term = max_long_term

        # Check if vector store is disabled via environment variable
        import os
        self._vector_store_disabled = os.environ.get('DISABLE_VECTOR_STORE', '').lower() == 'true'

        if self._vector_store_disabled:
            logger.info("Vector store disabled via DISABLE_VECTOR_STORE env var")

        logger.info(f"HybridContextManager initialized (lazy) for agent: {agent_id}")

    @property
    def long_term(self) -> ChromaMemoryStore | None:
        """Lazy-initialize ChromaDB-backed long-term memory store (sync)."""
        if self._vector_store_disabled:
            return None

        if self._chroma_store is None:
            self._chroma_store = ChromaMemoryStore(
                collection_name=self._chroma_config['collection_name']
            )
        return self._chroma_store

    async def _ensure_long_term(self) -> ChromaMemoryStore | None:
        """Async-safe long-term store accessor.

        First use constructs ChromaMemoryStore, which loads the ONNX embedding model
        (a second or two, or a model download on cold cache). Doing
        that on the event loop froze the whole server on the first turn; build it
        in a thread under a lock so concurrent first turns don't construct twice.
        """
        if self._vector_store_disabled:
            return None
        if self._chroma_store is not None:
            return self._chroma_store
        async with self._long_term_lock:
            if self._chroma_store is None:
                self._chroma_store = await asyncio.to_thread(
                    ChromaMemoryStore,
                    collection_name=self._chroma_config['collection_name'],
                )
        return self._chroma_store

    async def build_prompt(
        self,
        agent: 'Agent',
        session: 'Session'
    ) -> list[dict[str, Any]]:
        """
        Build context-aware prompt combining system, long-term, and short-term memory.

        Returns:
            List of message dicts formatted for LLM
        """
        prompt_parts = []

        # 1. System prompt with agent configuration
        system_content = agent.formatted_system_prompt()

        # 2. Saved memories about this person (cognitrix/memory/saved.py).
        # The old automatic store (raw messages in Chroma) is no longer read.
        user_id = str(getattr(session, 'user_id', None) or '')
        if user_id and not getattr(session, 'task_id', None) and not getattr(session, 'incognito', False):
            query = ''
            for m in reversed(session.chat or []):
                if str(m.get('role', '')).lower() == 'user' and m.get('type', 'text') == 'text':
                    query = str(m.get('content', '') or '')
                    break
            try:
                from cognitrix.memory.saved import prompt_block

                block = await prompt_block(
                    user_id, agent, query, session_id=str(getattr(session, 'id', '') or '') or None)
                if block:
                    system_content += "\n\n" + block
            except Exception:
                logger.exception("Could not load saved memories")
        if getattr(session, 'incognito', False):
            system_content += "\n\n" + INCOGNITO_NOTE

        prompt_parts.append({
            'role': 'system',
            'type': 'text',
            'content': system_content
        })

        # 3. Add short-term conversation history
        recent_messages = await self.short_term.build_prompt(agent, session)
        # Replace only the primary system prompt; the second system message is
        # turn-local media guidance and must survive the hybrid composition.
        media_and_history = recent_messages[1:]
        prompt_parts.extend(media_and_history)

        return prompt_parts

    def _format_memories(self, memories: list) -> str:
        """Format memories for inclusion in system prompt."""
        formatted = []
        for mem in memories:
            timestamp = mem.timestamp.strftime("%Y-%m-%d %H:%M") if mem.timestamp else "Unknown"
            formatted.append(f"[{timestamp}] {mem.content[:200]}...")

        return "\n".join(formatted)

    async def add_to_memory(self, message: dict[str, Any], user_id: str | None = None):
        """
        Add message to appropriate memory store.

        All messages go to short-term (via session).
        High-importance messages also go to long-term, tagged with the user
        they came from so retrieval stays per user.
        """
        # Score importance
        importance = self.importance_scorer.score(message)

        # Store in long-term if important enough and vector store is enabled
        if not self._vector_store_disabled and importance >= self.importance_threshold:
            try:
                lt = await self._ensure_long_term()
                if lt is None:
                    return
                await lt.store(
                    content=message.get('content', ''),
                    metadata={
                        'role': message.get('role', 'unknown'),
                        'type': message.get('type', 'text'),
                        'importance_score': importance,
                        'user_id': str(user_id or ''),
                    },
                    importance=importance
                )
                self._retrieval_cache = None
                logger.debug(f"Stored important message (score: {importance:.2f})")
            except Exception as e:
                logger.error(f"Failed to store in long-term memory: {e}")

    async def search_memory(self, query: str, k: int = 5) -> list[str]:
        """Search long-term memory for relevant information."""
        if self._vector_store_disabled:
            return []
        try:
            lt = await self._ensure_long_term()
            if lt is None:
                return []
            memories = await lt.retrieve(query, k=k)
            return [m.content for m in memories]
        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return []

    async def summarize_memory(self) -> str:
        """Get a summary of what the agent remembers."""
        try:
            lt = await self._ensure_long_term()
            if lt is None:
                return "No memories stored yet."
            recent = await lt.get_recent(n=20)
            if not recent:
                return "No memories stored yet."

            topics = set()
            for mem in recent:
                # Extract keywords (simple approach)
                words = mem.content.lower().split()
                topics.update(w for w in words if len(w) > 6)

            return f"Remembering {len(recent)} past interactions. Topics: {', '.join(list(topics)[:10])}"
        except Exception as e:
            logger.error(f"Memory summary failed: {e}")
            return "Memory unavailable."
