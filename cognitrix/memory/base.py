"""Abstract base class for memory systems."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str
    content: str
    metadata: dict[str, Any]
    timestamp: datetime
    importance: float
    embedding: Optional[list[float]] = None


class BaseMemory(ABC):
    """Abstract base for memory implementations."""

    @abstractmethod
    async def store(
        self,
        content: str,
        metadata: dict[str, Any],
        importance: float = 1.0
    ) -> str:
        """Store a memory. Returns memory ID."""
        pass

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        k: int = 5,
        filter_metadata: Optional[dict] = None
    ) -> list[MemoryEntry]:
        """Retrieve relevant memories."""
        pass

    @abstractmethod
    async def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """Get most recent memories."""
        pass

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        pass

    @abstractmethod
    async def clear(self):
        """Clear all memories."""
        pass
