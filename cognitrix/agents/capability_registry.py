"""Agent capability registry with embedding-based matching."""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from cognitrix.agents.base import Agent
from cognitrix.models.tool import Tool
from cognitrix.utils.embedding_model import get_embedding_model

logger = logging.getLogger('cognitrix.log')


@dataclass
class AgentCapability:
    """Capabilities and metadata for an agent."""
    agent: Agent
    embedding: np.ndarray
    description: str
    tools: list[str]
    specialties: list[str]


class CapabilityRegistry:
    """
    Registry of agent capabilities with semantic search.
    
    Uses embeddings to match tasks to the most suitable agent.
    """
    
    def __init__(self, embedding_model: str = "all-MiniLM-L6-v2"):
        """
        Initialize capability registry.
        
        Args:
            embedding_model: Sentence transformer model for embeddings (kept for API compat)
        """
        self.embedding_model = get_embedding_model(embedding_model)
        self.agents: dict[str, AgentCapability] = {}
        
        logger.info("CapabilityRegistry initialized")
    
    async def register_agent(self, agent: Agent):
        """
        Extract and store agent capabilities.
        
        Creates embedding from agent description, tools, and system prompt.
        """
        # Extract specialties from system prompt
        specialties = self._extract_specialties(agent.system_prompt)
        
        # Build capability description
        capability_text = f"""
Agent Name: {agent.name}
Description: {agent.system_prompt[:300]}
Specialties: {', '.join(specialties)}
Available Tools: {[t.name for t in agent.tools]}
Capabilities: Can perform tasks related to {', '.join(specialties)}
        """.strip()
        
        # Generate embedding
        embedding = self.embedding_model.encode(capability_text)
        
        # Store capability
        self.agents[agent.id] = AgentCapability(
            agent=agent,
            embedding=embedding,
            description=capability_text,
            tools=[t.name for t in agent.tools],
            specialties=specialties
        )
        
        logger.info(f"Registered agent: {agent.name} (specialties: {specialties})")
    
    def _extract_specialties(self, system_prompt: str) -> list[str]:
        """Extract specialties from system prompt."""
        prompt_lower = system_prompt.lower()
        
        specialty_keywords = {
            'code': ['code', 'programming', 'development', 'debugging', 'software'],
            'research': ['research', 'search', 'analysis', 'investigation'],
            'writing': ['write', 'content', 'documentation', 'blog', 'article'],
            'data': ['data', 'analytics', 'statistics', 'visualization'],
            'web': ['web', 'scraping', 'browser', 'internet', 'http'],
            'file': ['file', 'directory', 'filesystem', 'organize'],
            'math': ['math', 'calculation', 'computation', 'numerical']
        }
        
        specialties = []
        for specialty, keywords in specialty_keywords.items():
            if any(kw in prompt_lower for kw in keywords):
                specialties.append(specialty)
        
        return specialties if specialties else ['general']
    
    async def find_best_agent(
        self,
        task: str,
        required_tools: Optional[list[str]] = None
    ) -> tuple[Optional[Agent], float]:
        """
        Find best agent for a task using semantic similarity.
        
        Args:
            task: Task description
            required_tools: Optional list of required tool names
            
        Returns:
            Tuple of (best_agent, similarity_score)
        """
        if not self.agents:
            logger.warning("No agents registered in registry")
            return None, 0.0
        
        # Generate task embedding
        task_embedding = self.embedding_model.encode(task)
        
        best_agent = None
        best_score = -1.0
        
        for agent_id, capability in self.agents.items():
            # Calculate cosine similarity
            similarity = self._cosine_similarity(
                task_embedding,
                capability.embedding
            )
            
            # Boost score for tool matches
            tool_bonus = 0.0
            if required_tools:
                matching_tools = set(required_tools) & set(capability.tools)
                tool_bonus = len(matching_tools) * 0.15
            
            # Boost for keyword matches in task
            keyword_bonus = 0.0
            task_lower = task.lower()
            for specialty in capability.specialties:
                if specialty in task_lower:
                    keyword_bonus += 0.1
            
            total_score = similarity + tool_bonus + keyword_bonus
            
            if total_score > best_score:
                best_score = total_score
                best_agent = capability.agent
        
        logger.info(f"Best agent for task: {best_agent.name if best_agent else 'None'} (score: {best_score:.3f})")
        return best_agent, best_score
    
    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    
    async def find_agents_for_parallel(
        self,
        subtasks: list[str]
    ) -> dict[str, Agent]:
        """
        Find best agents for multiple subtasks.
        
        Returns:
            Mapping of subtask index to agent
        """
        assignments = {}
        
        for i, subtask in enumerate(subtasks):
            agent, score = await self.find_best_agent(subtask)
            if agent:
                assignments[i] = agent
        
        return assignments
    
    def get_agent_capabilities(self, agent_id: str) -> Optional[AgentCapability]:
        """Get capabilities for a specific agent."""
        return self.agents.get(agent_id)
    
    def list_registered_agents(self) -> list[str]:
        """List names of all registered agents."""
        return [cap.agent.name for cap in self.agents.values()]
    
    def clear(self):
        """Clear all registered agents."""
        self.agents.clear()
