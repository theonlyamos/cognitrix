"""Memory system for Cognitrix."""

from cognitrix.memory.base import BaseMemory, MemoryEntry

__all__ = [
    'BaseMemory',
    'MemoryEntry',
    'ChromaMemoryStore',
    'HybridContextManager',
    'ImportanceScorer',
]


def __getattr__(name: str):
    # Loaded on first use: they need chromadb, which the sandboxed computer's
    # copy of the CLI leaves out (sandbox/desktop/Dockerfile) to keep the
    # image small. Saved memories, the memory tool and the rest of the
    # package work without it.
    if name == 'ChromaMemoryStore':
        from cognitrix.memory.chroma_store import ChromaMemoryStore

        return ChromaMemoryStore
    if name in ('HybridContextManager', 'ImportanceScorer'):
        from cognitrix.memory import hybrid_context

        return getattr(hybrid_context, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
