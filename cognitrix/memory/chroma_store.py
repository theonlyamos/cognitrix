"""ChromaDB-based vector memory implementation."""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from cognitrix.memory.base import BaseMemory, MemoryEntry

logger = logging.getLogger('cognitrix.log')


class ChromaMemoryStore(BaseMemory):
    """ChromaDB-backed vector memory with local persistence."""

    def __init__(
        self,
        collection_name: str = "agent_memory",
        persist_directory: Optional[str] = None,
        embedding_model: str = "all-MiniLM-L6-v2"
    ):
        """
        Initialize ChromaDB memory store.

        Args:
            collection_name: Name of the ChromaDB collection
            persist_directory: Where to persist DB (default: ./chroma_db)
            embedding_model: Sentence transformer model name
        """
        self.collection_name = collection_name

        # Set up persistence
        if persist_directory is None:
            persist_directory = str(Path.cwd() / "chroma_db")

        self.persist_directory = persist_directory

        # Initialize ChromaDB client
        self.client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=persist_directory,
            anonymized_telemetry=False
        ))

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        # Initialize embedding model
        logger.info(f"Loading embedding model: {embedding_model}")
        self.embedding_model = SentenceTransformer(embedding_model)

        logger.info(f"ChromaMemoryStore initialized: {collection_name}")

    def _generate_id(self, content: str) -> str:
        """Generate deterministic ID from content."""
        return hashlib.md5(content.encode()).hexdigest()

    def _embed(self, text: str) -> list[float]:
        """Generate embedding for text."""
        embedding = self.embedding_model.encode(text)
        return embedding.tolist()

    async def store(
        self,
        content: str,
        metadata: dict[str, Any],
        importance: float = 1.0
    ) -> str:
        """Store a memory with embedding."""
        memory_id = self._generate_id(content + str(datetime.now()))

        # Generate embedding
        embedding = self._embed(content)

        # Prepare metadata
        chroma_metadata = {
            'content': content[:1000],  # Store truncated content in metadata
            'timestamp': datetime.now().isoformat(),
            'importance': importance,
            **{k: str(v) for k, v in metadata.items()}  # Chroma requires string values
        }

        # Add to collection
        self.collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[chroma_metadata]
        )

        logger.debug(f"Stored memory: {memory_id[:8]}...")
        return memory_id

    async def retrieve(
        self,
        query: str,
        k: int = 5,
        filter_metadata: Optional[dict] = None
    ) -> list[MemoryEntry]:
        """Retrieve relevant memories using semantic search."""
        # Generate query embedding
        query_embedding = self._embed(query)

        # Build where clause if filter provided
        where_clause = None
        if filter_metadata:
            where_clause = {k: str(v) for k, v in filter_metadata.items()}

        # Query collection
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where_clause
        )

        # Convert to MemoryEntry objects
        entries = []
        for i, memory_id in enumerate(results['ids'][0]):
            metadata = results['metadatas'][0][i] if results['metadatas'] else {}
            document = results['documents'][0][i] if results['documents'] else ""
            distance = results['distances'][0][i] if results['distances'] else 0

            entries.append(MemoryEntry(
                id=memory_id,
                content=document,
                metadata=metadata,
                timestamp=datetime.fromisoformat(metadata.get('timestamp', datetime.now().isoformat())),
                importance=float(metadata.get('importance', 1.0)),
                embedding=None  # Don't return embedding to save memory
            ))

        return entries

    async def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """Get most recent memories by timestamp."""
        # Get all entries (Chroma doesn't support sorting, so we filter client-side)
        results = self.collection.get()

        entries = []
        for i, memory_id in enumerate(results['ids']):
            metadata = results['metadatas'][i]
            document = results['documents'][i]

            entries.append(MemoryEntry(
                id=memory_id,
                content=document,
                metadata=metadata,
                timestamp=datetime.fromisoformat(metadata.get('timestamp', datetime.now().isoformat())),
                importance=float(metadata.get('importance', 1.0))
            ))

        # Sort by timestamp descending and take top n
        entries.sort(key=lambda x: x.timestamp, reverse=True)
        return entries[:n]

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        try:
            self.collection.delete(ids=[memory_id])
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            return False

    async def clear(self):
        """Clear all memories from collection."""
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"Cleared memory collection: {self.collection_name}")

    def persist(self):
        """Persist database to disk."""
        # Chroma with duckdb+parquet persists automatically
        pass
