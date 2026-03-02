"""Structured task planning with Pydantic validation."""

import json
import logging
from typing import Optional

from pydantic import ValidationError

from cognitrix.agents.base import Agent
from cognitrix.models.tool import Tool
from cognitrix.prompts.planning import TaskPlan, Step, PLANNING_SYSTEM_PROMPT, PLANNING_USER_TEMPLATE
from cognitrix.providers.base import LLM
from cognitrix.utils.retry import with_retry, RETRY_CONFIGS

logger = logging.getLogger('cognitrix.log')


class PlanningError(Exception):
    """Planning generation error."""
    pass


class StructuredPlanner:
    """Generates structured, validated plans using LLM."""
    
    def __init__(self, llm: LLM):
        self.llm = llm
    
    async def create_plan(
        self,
        task: str,
        available_agents: list[Agent],
        available_tools: list[Tool]
    ) -> TaskPlan:
        """
        Generate a structured plan for a task.
        
        Args:
            task: Description of the task
            available_agents: List of agents that can be assigned
            available_tools: List of tools available
            
        Returns:
            Validated TaskPlan
            
        Raises:
            PlanningError: If plan generation fails after retries
        """
        # Build prompt
        prompt = PLANNING_USER_TEMPLATE.format(
            task=task,
            agents=self._format_agents(available_agents),
            tools=self._format_tools(available_tools)
        )
        
        messages = [
            {'role': 'system', 'content': PLANNING_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt}
        ]
        
        # Generate with retry for valid JSON
        for attempt in range(3):
            try:
                # Generate plan
                response = await self.llm(messages, stream=False)
                
                # Extract response text
                if hasattr(response, 'llm_response'):
                    response_text = response.llm_response
                else:
                    # Handle generator/iterator
                    response_text = ""
                    async for chunk in response:
                        if hasattr(chunk, 'current_chunk'):
                            response_text += chunk.current_chunk
                        elif isinstance(chunk, str):
                            response_text += chunk
                
                # Parse JSON from response
                plan = self._parse_plan_response(response_text)
                
                # Validate references
                self._validate_references(plan, available_agents, available_tools)
                
                logger.info(f"Generated plan with {len(plan.steps)} steps")
                return plan
                
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(f"Plan parsing failed (attempt {attempt + 1}): {e}")
                
                if attempt == 2:
                    raise PlanningError(f"Failed to generate valid plan after 3 attempts: {e}")
                
                # Add error feedback to prompt
                messages.append({
                    'role': 'assistant',
                    'content': response_text if 'response_text' in locals() else ""
                })
                messages.append({
                    'role': 'user',
                    'content': f"The previous response was invalid: {e}. Please return ONLY valid JSON matching the schema."
                })
        
        raise PlanningError("Max retries exceeded for plan generation")
    
    def _parse_plan_response(self, response_text: str) -> TaskPlan:
        """Extract and parse JSON from LLM response."""
        import re
        
        # Try to extract JSON from markdown code blocks
        json_patterns = [
            r'```json\s*(.*?)\s*```',  # Markdown JSON block
            r'```\s*(.*?)\s*```',       # Generic code block
            r'(\{.*\})',                 # Raw JSON object
        ]
        
        for pattern in json_patterns:
            match = re.search(pattern, response_text, re.DOTALL)
            if match:
                json_str = match.group(1)
                try:
                    data = json.loads(json_str)
                    return TaskPlan(**data)
                except (json.JSONDecodeError, ValidationError):
                    continue
        
        # Try parsing entire response as JSON
        try:
            data = json.loads(response_text)
            return TaskPlan(**data)
        except (json.JSONDecodeError, ValidationError):
            pass
        
        raise PlanningError(f"Could not extract valid JSON from response: {response_text[:200]}...")
    
    def _validate_references(
        self,
        plan: TaskPlan,
        available_agents: list[Agent],
        available_tools: list[Tool]
    ):
        """Validate that plan references existing agents and tools."""
        agent_names = {a.name.lower() for a in available_agents}
        agent_names.add('auto')
        
        tool_names = {t.name.lower() for t in available_tools}
        
        for step in plan.steps:
            # Validate agent reference
            if step.assigned_agent.lower() not in agent_names:
                logger.warning(f"Step {step.step_number} references unknown agent: {step.assigned_agent}")
                step.assigned_agent = "auto"
            
            # Validate tool references
            for tool in step.required_tools:
                if tool.lower() not in tool_names:
                    logger.warning(f"Step {step.step_number} references unknown tool: {tool}")
    
    def _format_agents(self, agents: list[Agent]) -> str:
        """Format agent list for prompt."""
        lines = []
        for agent in agents:
            tools = [t.name for t in agent.tools]
            lines.append(f"- {agent.name}: {agent.system_prompt[:100]}... Tools: {tools}")
        return "\n".join(lines)
    
    def _format_tools(self, tools: list[Tool]) -> str:
        """Format tool list for prompt."""
        lines = []
        for tool in tools:
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)
    
    def get_execution_order(self, plan: TaskPlan) -> list[list[Step]]:
        """
        Convert plan to execution batches respecting dependencies.
        
        Returns:
            List of batches where steps in each batch can execute in parallel
        """
        completed = set()
        batches = []
        steps_by_num = {s.step_number: s for s in plan.steps}
        
        while len(completed) < len(plan.steps):
            # Find steps ready to execute
            ready = [
                s for s in plan.steps
                if s.step_number not in completed
                and all(d in completed for d in s.dependencies)
            ]
            
            if not ready:
                incomplete = [s.step_number for s in plan.steps if s.step_number not in completed]
                raise PlanningError(f"Circular dependency or stuck plan. Incomplete: {incomplete}")
            
            batches.append(ready)
            completed.update(s.step_number for s in ready)
        
        return batches
    
    def estimate_total_duration(self, plan: TaskPlan) -> str:
        """Estimate total task duration based on steps."""
        duration_map = {'short': 1, 'medium': 2, 'long': 3}
        
        # Calculate based on execution batches
        batches = self.get_execution_order(plan)
        total_units = sum(
            max(duration_map.get(s.estimated_duration, 2) for s in batch)
            for batch in batches
        )
        
        if total_units <= 3:
            return "short"
        elif total_units <= 8:
            return "medium"
        else:
            return "long"
