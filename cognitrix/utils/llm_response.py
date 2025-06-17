import json
import logging
from typing import Any

from odbms import Model

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class LLMResponse(Model):
    """Class to handle llm responses (now expects JSON output)"""

    llm_response: str | None = None
    """LLM response (raw string)"""

    chunks: list[str] = []
    """List of chunks"""
    current_chunk: str = ''
    """Current chunk"""

    result: str | None = None
    tool_calls: list[dict[str, Any]] = []
    artifacts: dict[str, Any] | list[dict[str, Any]] | None = None
    observation: str | None = None
    thought: str | list[str] | None = None
    mindspace: str | list[str] | None = None
    reflection: str | list[str] | None = None
    type: str | None = None
    before: str | None = None
    after: str | None = None
    scratchpad: str | None = None
    todo: list[str] | None = None
    response_overview: str | None = None
    task_summary: str | None = None
    evaluation: dict[str, Any] | None = None
    overall_assessment: str | None = None
    suggestions: list[str] | None = None
    finalscore: str | float | int | None = None
    title: str | None = None
    task_title: str | None = None
    task_description: str | None = None
    steps: list[str] | None = None
    name: str | None = None
    description: str | None = None
    tools: list[str] | None = None
    members: list[str] | None = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parse_llm_response()

    def add_chunk(self, chunk: str):
        self.current_chunk = chunk
        self.chunks.append(chunk)
        self.parse_llm_response()

    def parse_llm_response(self):
        self.llm_response = ''.join(self.chunks)
        if not self.llm_response:
            return
        try:
            data = json.loads(self.llm_response)
            # Map JSON fields to class attributes
            for key, value in data.items():
                # Support both 'tool_call' and 'tool_calls' for backward compatibility
                if key == 'tool_call':
                    self.tool_calls = value
                else:
                    setattr(self, key, value)
            # For result, try to set from 'result', 'response', or fallback to 'llm_response'
            self.result = data.get('result') or data.get('response') or self.result
        except json.JSONDecodeError:
            # logger.warning('Failed to parse LLM response as JSON. Returning raw response.')
            self.result = self.llm_response
        except Exception as e:
            logger.exception(e)
            self.result = self.llm_response

