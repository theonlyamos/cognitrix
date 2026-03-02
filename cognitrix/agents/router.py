"""Intelligent task routing to appropriate agents."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from cognitrix.agents.base import Agent
from cognitrix.agents.capability_registry import CapabilityRegistry

logger = logging.getLogger('cognitrix.log')


class RoutingStrategy(Enum):
    """Strategies for task routing."""
    SINGLE = "single"          # One agent handles entire task
    SEQUENTIAL = "sequential"  # Multiple agents in sequence
    PARALLEL = "parallel"      # Multiple agents in parallel
    HIERARCHICAL = "hierarchical"  # Leader delegates to specialists


class Complexity(Enum):
    """Task complexity levels."""
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass
class TaskAssignment:
    """Assignment of a task to an agent."""
    agent: Agent
    task: str
    subtask_id: Optional[int] = None
    dependencies: list[int] = None


@dataclass
class RoutePlan:
    """Complete routing plan for a task."""
    strategy: RoutingStrategy
    assignments: list[TaskAssignment]
    estimated_complexity: Complexity


class TaskDecomposer:
    """Breaks complex tasks into subtasks."""
    
    async def decompose(self, task: str, llm) -> list[str]:
        """
        Decompose task into subtasks using LLM.
        
        Returns:
            List of subtask descriptions
        """
        prompt = f"""Break down the following task into 2-5 subtasks.
Each subtask should be self-contained and executable by a single agent.

Task: {task}

Return ONLY a numbered list of subtasks, one per line.
Example:
1. Research current market trends
2. Analyze competitor pricing
3. Generate pricing recommendations
"""
        
        response = await llm([{'role': 'user', 'content': prompt}])
        
        # Parse response
        if hasattr(response, 'llm_response'):
            response_text = response.llm_response
        else:
            response_text = str(response)
        
        # Extract numbered items
        import re
        subtasks = re.findall(r'^\d+\.\s*(.+)$', response_text, re.MULTILINE)
        
        if not subtasks:
            # If no numbered list, treat as single task
            return [task]
        
        return subtasks


class ComplexityAssessor:
    """Assesses task complexity."""
    
    COMPLEXITY_INDICATORS = {
        'simple': ['simple', 'basic', 'quick', 'easy', 'one', 'single'],
        'complex': ['complex', 'comprehensive', 'detailed', 'analysis', 'research', 'multiple', 'steps']
    }
    
    def assess(self, task: str) -> Complexity:
        """Assess task complexity based on keywords and length."""
        task_lower = task.lower()
        word_count = len(task.split())
        
        # Check for complexity indicators
        simple_score = sum(1 for ind in self.COMPLEXITY_INDICATORS['simple'] if ind in task_lower)
        complex_score = sum(1 for ind in self.COMPLEXITY_INDICATORS['complex'] if ind in task_lower)
        
        # Factor in length
        if word_count < 10:
            simple_score += 1
        elif word_count > 30:
            complex_score += 1
        
        # Determine complexity
        if complex_score > simple_score:
            return Complexity.COMPLEX
        elif simple_score > complex_score or word_count < 15:
            return Complexity.SIMPLE
        else:
            return Complexity.MODERATE


class AgentRouter:
    """
    Routes tasks to appropriate agents based on capabilities.
    
    Uses embeddings to match tasks to agents and can decompose
    complex tasks for parallel or sequential execution.
    """
    
    def __init__(self):
        self.registry = CapabilityRegistry()
        self.decomposer = TaskDecomposer()
        self.assessor = ComplexityAssessor()
    
    async def route_task(
        self,
        task: str,
        available_agents: list[Agent],
        llm=None
    ) -> RoutePlan:
        """
        Route a task to the best agent(s).
        
        Args:
            task: Task description
            available_agents: List of available agents
            llm: LLM for decomposition (optional)
            
        Returns:
            RoutePlan with strategy and assignments
        """
        # Register all available agents
        for agent in available_agents:
            await self.registry.register_agent(agent)
        
        # Assess complexity
        complexity = self.assessor.assess(task)
        logger.info(f"Task complexity: {complexity.value}")
        
        # Route based on complexity
        if complexity == Complexity.SIMPLE:
            return await self._route_simple(task)
        elif complexity == Complexity.MODERATE:
            return await self._route_moderate(task, llm)
        else:  # COMPLEX
            return await self._route_complex(task, llm)
    
    async def _route_simple(self, task: str) -> RoutePlan:
        """Route simple task to single best agent."""
        agent, score = await self.registry.find_best_agent(task)
        
        if not agent:
            raise RoutingError("No suitable agent found for task")
        
        return RoutePlan(
            strategy=RoutingStrategy.SINGLE,
            assignments=[TaskAssignment(agent=agent, task=task)],
            estimated_complexity=Complexity.SIMPLE
        )
    
    async def _route_moderate(self, task: str, llm) -> RoutePlan:
        """Route moderate task, possibly with decomposition."""
        if not llm:
            # Can't decompose without LLM, treat as simple
            return await self._route_simple(task)
        
        # Try decomposition
        subtasks = await self.decomposer.decompose(task, llm)
        
        if len(subtasks) == 1:
            # Not decomposable, treat as simple
            return await self._route_simple(task)
        
        # Find agents for each subtask
        assignments = []
        for i, subtask in enumerate(subtasks):
            agent, score = await self.registry.find_best_agent(subtask)
            if agent:
                assignments.append(TaskAssignment(
                    agent=agent,
                    task=subtask,
                    subtask_id=i,
                    dependencies=[] if i == 0 else [i-1]  # Sequential by default
                ))
        
        return RoutePlan(
            strategy=RoutingStrategy.SEQUENTIAL,
            assignments=assignments,
            estimated_complexity=Complexity.MODERATE
        )
    
    async def _route_complex(self, task: str, llm) -> RoutePlan:
        """Route complex task with parallel execution where possible."""
        if not llm:
            return await self._route_moderate(task, llm)
        
        # Decompose into subtasks
        subtasks = await self.decomposer.decompose(task, llm)
        
        # Find agents for each subtask
        assignments = []
        for i, subtask in enumerate(subtasks):
            agent, score = await self.registry.find_best_agent(subtask)
            if agent:
                # For complex tasks, analyze dependencies
                # For now, assume some can be parallel
                dependencies = []
                if i > 0 and not self._can_parallelize(subtask, subtasks[i-1]):
                    dependencies = [i-1]
                
                assignments.append(TaskAssignment(
                    agent=agent,
                    task=subtask,
                    subtask_id=i,
                    dependencies=dependencies
                ))
        
        # Determine if any can run in parallel
        has_parallel = any(not a.dependencies for a in assignments)
        strategy = RoutingStrategy.PARALLEL if has_parallel else RoutingStrategy.SEQUENTIAL
        
        return RoutePlan(
            strategy=strategy,
            assignments=assignments,
            estimated_complexity=Complexity.COMPLEX
        )
    
    def _can_parallelize(self, task_a: str, task_b: str) -> bool:
        """
        Determine if two tasks can run in parallel.
        
        Simple heuristic: tasks are independent if they don't
        share obvious dependencies.
        """
        # Check for dependency keywords
        dependent_keywords = ['after', 'then', 'once', 'following', 'based on']
        
        # If task_b mentions dependency on previous, it's sequential
        task_b_lower = task_b.lower()
        if any(kw in task_b_lower for kw in dependent_keywords):
            return False
        
        return True


class RoutingError(Exception):
    """Routing error."""
    pass
