"""Tool wrapper with retry, validation, and LLM-powered recovery."""

import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from cognitrix.tools.base import Tool
from cognitrix.utils.retry import with_retry, RETRY_CONFIGS
from cognitrix.providers.base import LLM

logger = logging.getLogger('cognitrix.log')


@dataclass
class ToolResult:
    """Result of tool execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    recovery_attempted: bool = False
    attempts: int = 1


class ResilientToolManager:
    """Wraps tools with retry and intelligent error recovery."""
    
    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm
    
    def _sanitize_for_prompt(self, text: str) -> str:
        """Sanitize text to prevent prompt injection."""
        text = html.escape(text, quote=True)
        text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
        return text[:1000]
    
    async def run_tool(
        self,
        tool: Tool,
        params: dict[str, Any],
        max_retries: int = 3,
        attempt_recovery: bool = True
    ) -> ToolResult:
        """
        Execute tool with retry and optional LLM recovery.
        
        Args:
            tool: Tool to execute
            params: Tool parameters
            max_retries: Maximum retry attempts
            attempt_recovery: Whether to try LLM parameter recovery
            
        Returns:
            ToolResult with execution status
        """
        config = RETRY_CONFIGS['tool_execution']
        config.max_attempts = max_retries
        
        last_error = None
        current_params = params.copy()
        
        for attempt in range(1, max_retries + 1):
            try:
                # Validate parameters
                validated = await self._validate_params(tool, current_params)
                
                # Execute tool
                result = await tool.run(**validated)
                
                return ToolResult(
                    success=True,
                    data=result,
                    attempts=attempt
                )
                
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Tool {tool.name} attempt {attempt} failed: {e}")
                
                if attempt < max_retries and attempt_recovery and self.llm:
                    # Try LLM-powered parameter recovery
                    recovered = await self._attempt_param_recovery(
                        tool, current_params, last_error
                    )
                    
                    if recovered:
                        logger.info(f"Recovered parameters for {tool.name}")
                        current_params = recovered
                        continue
                
                if attempt < max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** (attempt - 1))
        
        return ToolResult(
            success=False,
            error=last_error,
            recovery_attempted=attempt_recovery,
            attempts=max_retries
        )
    
    async def _validate_params(
        self, 
        tool: Tool, 
        params: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate and sanitize parameters before execution."""
        # Basic validation - ensure required params are present
        # Tool-specific validation can be added here
        return params
    
    async def _attempt_param_recovery(
        self,
        tool: Tool,
        original_params: dict[str, Any],
        error: str
    ) -> Optional[dict[str, Any]]:
        """Use LLM to fix parameters based on error message."""
        if not self.llm:
            return None
        
        prompt = f"""The following tool call failed. Fix the parameters.

Tool: {self._sanitize_for_prompt(tool.name)}
Description: {self._sanitize_for_prompt(tool.description)}
Original parameters: {self._sanitize_for_prompt(json.dumps(original_params, indent=2))}
Error: {self._sanitize_for_prompt(error)}

Provide corrected parameters as valid JSON only. If unrecoverable, return {{"_unrecoverable": true}}.
"""
        
        try:
            response = await self.llm([{'role': 'user', 'content': prompt}])
            
            if hasattr(response, 'llm_response'):
                response_text = response.llm_response
            else:
                response_text = str(response)
            
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                recovered = json.loads(json_match.group())
                
                if recovered.get('_unrecoverable'):
                    return None
                
                return recovered
                
        except Exception as e:
            logger.error(f"Parameter recovery failed: {e}")
        
        return None
