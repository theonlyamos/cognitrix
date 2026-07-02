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
    reasoning_chunks: list[str] = []
    """Reasoning/thinking chunks (streamed separately)"""
    current_chunk: str = ''
    """Current chunk (content or wrapped reasoning for display)"""

    result: str | None = None
    error: str | None = None
    """Set when the response represents a provider/transport error, not a real answer."""
    usage: dict[str, int] | None = None
    """Real token usage from the provider: {'prompt_tokens': N, 'completion_tokens': N}."""
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
    reasoning: str | None = None
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
        # Append incrementally instead of re-joining the whole chunk list on
        # every streamed chunk (that was O(n^2) over a stream).
        self.llm_response = (self.llm_response or '') + chunk
        self._parse_structure()

    def add_reasoning_chunk(self, chunk: str):
        """Accumulate reasoning; caller sets current_chunk to incremental display."""
        self.reasoning_chunks.append(chunk)
        self.reasoning = (self.reasoning or '') + chunk

    def parse_llm_response(self):
        # Full rebuild from chunks (used at construction time).
        self.llm_response = ''.join(self.chunks)
        self._parse_structure()

    def _parse_structure(self):
        if not self.llm_response:
            return
        stripped = self.llm_response.strip()
        # Only attempt a JSON parse when the buffer looks like a complete JSON
        # object/array. This avoids an O(n^2) json.loads over the growing buffer
        # on every streamed chunk — the common case is prose, and mid-stream JSON
        # is incomplete anyway.
        if not (stripped[:1] in ('{', '[') and stripped[-1:] in ('}', ']')):
            self.result = self.llm_response
            return
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                # Map JSON fields to attributes (tool_calls come from the native
                # provider path, not content).
                for key, value in data.items():
                    if key in ('tool_call', 'tool_calls'):
                        continue
                    setattr(self, key, value)
                self.result = data.get('result') or data.get('response') or self.result
            else:
                self.result = self.llm_response
        except json.JSONDecodeError:
            self.result = self.llm_response
        except Exception as e:
            logger.exception(e)
            self.result = self.llm_response

