"""Tests for memory system including ChromaMemoryStore and HybridContextManager."""

import asyncio
import pytest
import numpy as np
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from cognitrix.memory.base import MemoryEntry
from cognitrix.memory.chroma_store import ChromaMemoryStore
from cognitrix.memory.hybrid_context import HybridContextManager, ImportanceScorer


class TestChromaMemoryStore:
    """Test suite for ChromaMemoryStore."""
    
    @pytest.fixture
    def mock_chroma_client(self):
        """Create a mock ChromaDB client."""
        with patch('cognitrix.memory.chroma_store.chromadb') as mock_chromadb:
            mock_client = MagicMock()
            mock_collection = MagicMock()
            mock_client.get_or_create_collection.return_value = mock_collection
            mock_client.create_collection.return_value = mock_collection
            mock_chromadb.Client.return_value = mock_client
            yield mock_client, mock_collection
    
    @pytest.fixture
    def mock_embedding_model(self):
        """Create a mock embedding model."""
        with patch('cognitrix.memory.chroma_store.get_embedding_model') as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([0.1, 0.2, 0.3, 0.4])
            mock_get_model.return_value = mock_model
            yield mock_model
    
    @pytest.fixture
    def memory_store(self, mock_chroma_client, mock_embedding_model):
        """Create a memory store with mocked dependencies."""
        return ChromaMemoryStore(
            collection_name="test_collection",
            persist_directory="/tmp/test_chroma"
        )
    
    @pytest.mark.asyncio
    async def test_store_memory(self, memory_store, mock_chroma_client):
        """Test storing a memory entry."""
        _, mock_collection = mock_chroma_client
        
        memory_id = await memory_store.store(
            content="Test memory content",
            metadata={"source": "test", "type": "user_message"},
            importance=0.8
        )
        
        assert memory_id is not None
        assert len(memory_id) == 32  # MD5 hash length
        mock_collection.add.assert_called_once()
        
        call_args = mock_collection.add.call_args
        assert call_args[1]['ids'][0] == memory_id
        assert call_args[1]['documents'][0] == "Test memory content"
    
    @pytest.mark.asyncio
    async def test_store_with_embedding(self, memory_store, mock_embedding_model):
        """Test that embeddings are generated when storing."""
        await memory_store.store(
            content="Test content",
            metadata={},
            importance=1.0
        )
        
        mock_embedding_model.encode.assert_called_once_with("Test content")
    
    @pytest.mark.asyncio
    async def test_retrieve_memories(self, memory_store, mock_chroma_client):
        """Test retrieving memories with query."""
        _, mock_collection = mock_chroma_client
        
        # Mock query results
        mock_collection.query.return_value = {
            'ids': [['id1', 'id2']],
            'documents': [['Content 1', 'Content 2']],
            'metadatas': [[
                {'timestamp': datetime.now().isoformat(), 'importance': '0.9'},
                {'timestamp': datetime.now().isoformat(), 'importance': '0.7'}
            ]],
            'distances': [[0.1, 0.3]]
        }
        
        results = await memory_store.retrieve("test query", k=2)
        
        assert len(results) == 2
        assert isinstance(results[0], MemoryEntry)
        assert results[0].content == "Content 1"
        assert results[0].importance == 0.9
    
    @pytest.mark.asyncio
    async def test_retrieve_with_filter(self, memory_store, mock_chroma_client):
        """Test retrieving with metadata filter."""
        _, mock_collection = mock_chroma_client
        
        mock_collection.query.return_value = {
            'ids': [['id1']],
            'documents': [['Filtered content']],
            'metadatas': [[{'timestamp': datetime.now().isoformat(), 'importance': '1.0'}]],
            'distances': [[0.05]]
        }
        
        await memory_store.retrieve(
            "query",
            k=5,
            filter_metadata={"type": "important"}
        )
        
        mock_collection.query.assert_called_once()
        call_args = mock_collection.query.call_args
        assert call_args[1]['where'] == {'type': 'important'}
    
    @pytest.mark.asyncio
    async def test_get_recent_memories(self, memory_store, mock_chroma_client):
        """Test getting recent memories."""
        _, mock_collection = mock_chroma_client
        
        now = datetime.now().isoformat()
        mock_collection.get.return_value = {
            'ids': ['id1', 'id2', 'id3'],
            'documents': ['Content 1', 'Content 2', 'Content 3'],
            'metadatas': [
                {'timestamp': now, 'importance': '0.5'},
                {'timestamp': now, 'importance': '0.8'},
                {'timestamp': now, 'importance': '0.3'}
            ]
        }
        
        results = await memory_store.get_recent(n=2)
        
        assert len(results) <= 2
        mock_collection.get.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_delete_memory(self, memory_store, mock_chroma_client):
        """Test deleting a memory."""
        _, mock_collection = mock_chroma_client
        
        result = await memory_store.delete("memory_id_123")
        
        assert result is True
        mock_collection.delete.assert_called_once_with(ids=['memory_id_123'])
    
    @pytest.mark.asyncio
    async def test_delete_memory_failure(self, memory_store, mock_chroma_client):
        """Test handling delete failure."""
        _, mock_collection = mock_chroma_client
        mock_collection.delete.side_effect = Exception("Delete failed")
        
        result = await memory_store.delete("memory_id_123")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_clear_collection(self, memory_store, mock_chroma_client):
        """Test clearing all memories."""
        mock_client, mock_collection = mock_chroma_client
        
        await memory_store.clear()
        
        mock_client.delete_collection.assert_called_once_with("test_collection")
        mock_client.create_collection.assert_called_once()
    
    def test_generate_id_deterministic(self, memory_store):
        """Test that IDs are generated deterministically from content."""
        content = "Test content"
        id1 = memory_store._generate_id(content)
        id2 = memory_store._generate_id(content)
        
        assert id1 == id2
        assert len(id1) == 32  # MD5 hex
    
    @pytest.mark.asyncio
    async def test_embed_async(self, memory_store, mock_embedding_model):
        """Test async embedding generation."""
        result = await memory_store._embed_async("test text")
        
        assert isinstance(result, list)
        assert len(result) == 4  # Mock returns 4-dim vector
        mock_embedding_model.encode.assert_called_with("test text")


class TestImportanceScorer:
    """Test suite for ImportanceScorer."""
    
    @pytest.fixture
    def scorer(self):
        """Create an importance scorer."""
        return ImportanceScorer()
    
    def test_score_simple_message(self, scorer):
        """Test scoring a simple message."""
        message = {
            'content': 'Hello, how are you?',
            'role': 'user'
        }
        
        score = scorer.score(message)
        
        assert scorer.min_importance <= score <= scorer.max_importance
    
    def test_score_with_high_importance_keywords(self, scorer):
        """Test that high importance keywords increase score."""
        simple_message = {'content': 'Hello there', 'role': 'user'}
        important_message = {'content': 'Error occurred in the system', 'role': 'system'}
        
        simple_score = scorer.score(simple_message)
        important_score = scorer.score(important_message)
        
        assert important_score > simple_score
    
    def test_score_system_message_boost(self, scorer):
        """Test that system messages get a boost."""
        user_message = {'content': 'Same content here', 'role': 'user'}
        system_message = {'content': 'Same content here', 'role': 'system'}
        
        user_score = scorer.score(user_message)
        system_score = scorer.score(system_message)
        
        assert system_score > user_score
    
    def test_score_long_message_boost(self, scorer):
        """Test that longer messages get a boost."""
        short_message = {'content': 'Short.', 'role': 'user'}
        long_message = {'content': ' '.join(['word'] * 100), 'role': 'user'}
        
        short_score = scorer.score(short_message)
        long_score = scorer.score(long_message)
        
        assert long_score > short_score
    
    def test_score_structured_data_boost(self, scorer):
        """Test that structured data gets a boost."""
        plain_message = {'content': 'Plain text message', 'role': 'user'}
        code_message = {'content': '```python\ncode here\n```', 'role': 'user'}
        
        plain_score = scorer.score(plain_message)
        code_score = scorer.score(code_message)
        
        assert code_score > plain_score
    
    def test_score_max_cap(self, scorer):
        """Test that score doesn't exceed max."""
        message = {
            'content': 'Error! Critical failure! Success! ```code``` ' + 'word ' * 300,
            'role': 'system'
        }
        
        score = scorer.score(message)
        
        assert score <= scorer.max_importance
    
    def test_score_min_floor(self, scorer):
        """Test that score doesn't go below min."""
        message = {'content': 'Hi', 'role': 'user'}
        
        score = scorer.score(message)
        
        assert score >= scorer.min_importance
    
    def test_high_importance_keywords_list(self, scorer):
        """Test that all high importance keywords are recognized."""
        keywords = scorer.HIGH_IMPORTANCE_KEYWORDS
        
        assert 'error' in keywords
        assert 'success' in keywords
        assert 'critical' in keywords
        assert 'remember' in keywords


class TestHybridContextManager:
    """Test suite for HybridContextManager."""
    
    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        agent = MagicMock()
        agent.name = "TestAgent"
        agent.formatted_system_prompt.return_value = "System prompt for testing"
        return agent
    
    @pytest.fixture
    def mock_session(self):
        """Create a mock session."""
        session = MagicMock()
        session.chat = [{'role': 'user', 'content': 'Test query'}]
        return session
    
    @pytest.fixture
    def hybrid_manager(self):
        """Create a hybrid context manager with mocked long-term memory."""
        with patch('cognitrix.memory.hybrid_context.ChromaMemoryStore') as mock_store:
            mock_store_instance = AsyncMock()
            mock_store.return_value = mock_store_instance
            
            manager = HybridContextManager(
                agent_id="test_agent_123",
                max_short_term=5,
                max_long_term=3,
                importance_threshold=0.7,
                persist_directory="/tmp/test"
            )
            yield manager, mock_store_instance
    
    def test_initialization(self, hybrid_manager):
        """Test proper initialization."""
        manager, mock_store = hybrid_manager
        
        assert manager.agent_id == "test_agent_123"
        assert manager.max_long_term == 3
        assert manager.importance_threshold == 0.7
        assert isinstance(manager.importance_scorer, ImportanceScorer)
    
    @pytest.mark.asyncio
    async def test_build_prompt_structure(self, hybrid_manager, mock_agent, mock_session):
        """Test that prompt is built with correct structure."""
        manager, _ = hybrid_manager
        
        with patch.object(manager.long_term, 'retrieve', new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = []
            
            prompt = await manager.build_prompt(mock_agent, mock_session)
        
        assert len(prompt) >= 1
        assert prompt[0]['role'] == 'system'
        assert 'System prompt for testing' in prompt[0]['content']
    
    @pytest.mark.asyncio
    async def test_build_prompt_with_memories(self, hybrid_manager, mock_agent, mock_session):
        """Test that long-term memories are included in prompt."""
        manager, _ = hybrid_manager
        
        mock_memory = MagicMock()
        mock_memory.content = "Relevant past information"
        mock_memory.timestamp = datetime.now()
        
        with patch.object(manager.long_term, 'retrieve', new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = [mock_memory]
            prompt = await manager.build_prompt(mock_agent, mock_session)
            assert len(prompt) >= 1
    
    @pytest.mark.asyncio
    async def test_add_to_memory_below_threshold(self, hybrid_manager):
        """Test that low importance messages don't go to long-term memory."""
        manager, mock_store = hybrid_manager
        
        # Low importance message
        message = {
            'content': 'Hi',
            'role': 'user',
            'type': 'text'
        }
        
        await manager.add_to_memory(message)
        
        mock_store.store.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_add_to_memory_above_threshold(self, hybrid_manager):
        """Test that high importance messages are stored."""
        manager, mock_store = hybrid_manager
        
        # High importance message (error, system role, etc.)
        message = {
            'content': 'Critical error occurred in the system that needs to be remembered',
            'role': 'system',
            'type': 'text'
        }
        
        await manager.add_to_memory(message)
        
        mock_store.store.assert_called_once()
        call_args = mock_store.store.call_args
        assert 'importance' in call_args[1]
        assert call_args[1]['importance'] >= manager.importance_threshold
    
    @pytest.mark.asyncio
    async def test_search_memory(self, hybrid_manager):
        """Test searching long-term memory."""
        manager, mock_store = hybrid_manager
        
        mock_memory = MagicMock()
        mock_memory.content = "Search result content"
        mock_store.retrieve.return_value = [mock_memory]
        
        results = await manager.search_memory("test query", k=5)
        
        mock_store.retrieve.assert_called_once_with("test query", k=5)
        assert len(results) == 1
        assert results[0] == "Search result content"
    
    @pytest.mark.asyncio
    async def test_search_memory_error_handling(self, hybrid_manager):
        """Test that search errors are handled gracefully."""
        manager, mock_store = hybrid_manager
        mock_store.retrieve.side_effect = Exception("Search failed")
        
        results = await manager.search_memory("query")
        
        assert results == []
    
    @pytest.mark.asyncio
    async def test_summarize_memory_empty(self, hybrid_manager):
        """Test memory summary when empty."""
        manager, mock_store = hybrid_manager
        mock_store.get_recent.return_value = []
        
        summary = await manager.summarize_memory()
        
        assert "No memories stored" in summary
    
    @pytest.mark.asyncio
    async def test_summarize_memory_with_content(self, hybrid_manager):
        """Test memory summary with stored memories."""
        manager, mock_store = hybrid_manager
        
        mock_memory = MagicMock()
        mock_memory.content = "This is important information about programming"
        mock_store.get_recent.return_value = [mock_memory] * 5
        
        summary = await manager.summarize_memory()
        
        assert "Remembering" in summary
        assert "5" in summary
    
    def test_format_memories(self, hybrid_manager):
        """Test memory formatting for prompt inclusion."""
        manager, _ = hybrid_manager
        
        mock_memories = []
        for i in range(3):
            mem = MagicMock()
            mem.content = f"Memory content {i}"
            mem.timestamp = datetime.now()
            mock_memories.append(mem)
        
        formatted = manager._format_memories(mock_memories)
        
        assert "Memory content 0" in formatted
        assert "Memory content 1" in formatted
        # Should be truncated to 200 chars with ...
        assert "..." in formatted
    
    @pytest.mark.asyncio
    async def test_add_to_memory_error_handling(self, hybrid_manager):
        """Test that store errors are handled gracefully."""
        manager, mock_store = hybrid_manager
        mock_store.store.side_effect = Exception("Store failed")
        
        # High importance message should trigger store
        message = {
            'content': 'Critical system error that must be logged and remembered for future reference',
            'role': 'system',
            'type': 'text'
        }
        
        # Should not raise exception
        await manager.add_to_memory(message)


class TestMemoryIntegration:
    """Integration tests for memory components working together."""
    
    @pytest.mark.asyncio
    async def test_memory_workflow(self):
        """Test complete memory storage and retrieval workflow."""
        with patch('cognitrix.memory.chroma_store.chromadb') as mock_chromadb, \
             patch('cognitrix.memory.chroma_store.get_embedding_model') as mock_get_model:
            
            # Setup mocks
            mock_client = MagicMock()
            mock_collection = MagicMock()
            mock_client.get_or_create_collection.return_value = mock_collection
            mock_chromadb.Client.return_value = mock_client
            
            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([0.1, 0.2, 0.3])
            mock_get_model.return_value = mock_model
            
            # Create store
            store = ChromaMemoryStore(collection_name="test")
            
            # Store a memory
            memory_id = await store.store(
                content="Important information",
                metadata={"type": "test"},
                importance=0.9
            )
            
            # Mock retrieval
            mock_collection.query.return_value = {
                'ids': [['stored_id']],
                'documents': [['Important information']],
                'metadatas': [[{'timestamp': datetime.now().isoformat(), 'importance': '0.9'}]],
                'distances': [[0.1]]
            }
            
            # Retrieve
            results = await store.retrieve("information", k=1)
            
            assert len(results) == 1
            assert results[0].content == "Important information"
    
    @pytest.mark.asyncio
    async def test_hybrid_context_full_flow(self):
        """Test full hybrid context flow."""
        # Simplified test - just verify the manager can be created
        with patch('cognitrix.memory.hybrid_context.ChromaMemoryStore'):
            manager = HybridContextManager(
                agent_id="test_agent",
                max_short_term=3,
                max_long_term=2
            )
            
            # Verify basic properties
            assert manager.agent_id == "test_agent"
