"""Tool wrapper with retry, validation, and LLM-powered recovery."""

import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from cognitrix.providers.base import LLM
from cognitrix.errors import ExecutionControlError
from cognitrix.tasks.accounting import current_task_accounting
from cognitrix.tools.base import Tool

logger = logging.getLogger('cognitrix.log')


@dataclass
class ToolResult:
    """Result of tool execution."""
    success: bool
    data: Any = None
    error: str | None = None
    recovery_attempted: bool = False
    attempts: int = 1


class ResilientToolManager:
    """Wraps tools with retry and intelligent error recovery."""

    def __init__(self, llm: LLM | None = None):
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
        last_error = None
        current_params = params.copy()
        attempt = 0

        for attempt in range(1, max_retries + 1):
            accounting = current_task_accounting()
            if accounting is not None:
                await accounting.consume_tool_attempt(first_for_call=attempt == 1)
            try:
                # Validate parameters
                validated = await self._validate_params(tool, current_params)

                # Execute tool
                operation = tool.run(**validated)
                if accounting is not None:
                    result = await accounting.wait_within_wall(operation)
                else:
                    result = await operation

                return ToolResult(
                    success=True,
                    data=result,
                    attempts=attempt
                )

            except ExecutionControlError:
                raise
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Tool {tool.name} attempt {attempt} failed: {e}")

                # Only retry with CORRECTED parameters from LLM recovery. Never
                # blindly re-run the same call: a non-idempotent tool (file write,
                # HTTP POST, shell command) could apply its side effect twice.
                recovered = None
                if attempt < max_retries and attempt_recovery and self.llm:
                    recovered = await self._attempt_param_recovery(
                        tool, current_params, last_error
                    )

                if recovered:
                    logger.info(f"Recovered parameters for {tool.name}; retrying with corrected args")
                    current_params = recovered
                    continue

                break

        return ToolResult(
            success=False,
            error=last_error,
            recovery_attempted=attempt_recovery,
            attempts=attempt
        )

    async def _validate_params(
        self,
        tool: Tool,
        params: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate and sanitize parameters before execution."""
        validator = getattr(tool, 'validate_parameters', None)
        if callable(validator):
            return validator(params)
        return params

    async def _attempt_param_recovery(
        self,
        tool: Tool,
        original_params: dict[str, Any],
        error: str
    ) -> dict[str, Any] | None:
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

        except ExecutionControlError:
            raise
        except Exception as e:
            logger.error(f"Parameter recovery failed: {e}")

        return None
