"""Hybrid context manager combining short-term and long-term memory."""

import logging
from typing import TYPE_CHECKING, Any

from cognitrix.memory.base import BaseMemory
from cognitrix.memory.chroma_store import ChromaMemoryStore
from cognitrix.sessions.context import SlidingWindowContextManager

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')


class ImportanceScorer:
    """Scores the importance of a message for memory storage."""

    HIGH_IMPORTANCE_KEYWORDS = [
        'error', 'exception', 'failed', 'success', 'completed',
        'important', 'critical', 'key', 'result', 'conclusion',
        'remember', 'note', 'save', 'permanent'
    ]

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


class HybridContextManager:
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
        importance_threshold: float = 0.7,
        persist_directory: str = None
    ):
        """
        Initialize hybrid context manager.

        Args:
            agent_id: Unique ID for agent's memory collection
            max_short_term: Number of recent messages to keep in context
            max_long_term: Number of relevant memories to retrieve
            importance_threshold: Minimum importance to store in long-term
            persist_directory: Where to persist ChromaDB
        """
        self.agent_id = agent_id
        self.short_term = SlidingWindowContextManager(max_short_term)
        self.long_term = ChromaMemoryStore(
            collection_name=f"agent_{agent_id}",
            persist_directory=persist_directory
        )
        self.importance_scorer = ImportanceScorer()
        self.importance_threshold = importance_threshold
        self.max_long_term = max_long_term

        logger.info(f"HybridContextManager initialized for agent: {agent_id}")

    def build_prompt(
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

        # 2. Retrieve relevant long-term memories
        long_term_memories = []
        if session.chat:
            # Use last user message as query
            last_message = session.chat[-1]
            query = last_message.get('content', '')

            if query:
                try:
                    memories = self.long_term.retrieve(query, k=self.max_long_term)
                    if memories:
                        memory_context = self._format_memories(memories)
                        system_content += f"\n\n## Relevant Past Context\n{memory_context}"
                except Exception as e:
                    logger.error(f"Failed to retrieve memories: {e}")

        prompt_parts.append({
            'role': 'system',
            'type': 'text',
            'content': system_content
        })

        # 3. Add short-term conversation history
        recent_messages = self.short_term.build_prompt(agent, session)
        # Skip the system message from short_term (we built our own)
        prompt_parts.extend(recent_messages[1:])

        return prompt_parts

    def _format_memories(self, memories: list) -> str:
        """Format memories for inclusion in system prompt."""
        formatted = []
        for mem in memories:
            timestamp = mem.timestamp.strftime("%Y-%m-%d %H:%M") if mem.timestamp else "Unknown"
            formatted.append(f"[{timestamp}] {mem.content[:200]}...")

        return "\n".join(formatted)

    async def add_to_memory(self, message: dict[str, Any]):
        """
        Add message to appropriate memory store.

        All messages go to short-term (via session).
        High-importance messages also go to long-term.
        """
        # Score importance
        importance = self.importance_scorer.score(message)

        # Store in long-term if important enough
        if importance >= self.importance_threshold:
            try:
                await self.long_term.store(
                    content=message.get('content', ''),
                    metadata={
                        'role': message.get('role', 'unknown'),
                        'type': message.get('type', 'text'),
                        'importance_score': importance
                    },
                    importance=importance
                )
                logger.debug(f"Stored important message (score: {importance:.2f})")
            except Exception as e:
                logger.error(f"Failed to store in long-term memory: {e}")

    async def search_memory(self, query: str, k: int = 5) -> list[str]:
        """Search long-term memory for relevant information."""
        try:
            memories = await self.long_term.retrieve(query, k=k)
            return [m.content for m in memories]
        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return []

    async def summarize_memory(self) -> str:
        """Get a summary of what the agent remembers."""
        try:
            recent = await self.long_term.get_recent(n=20)
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
