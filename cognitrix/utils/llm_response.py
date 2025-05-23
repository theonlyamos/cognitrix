import json
from typing import Any, Dict, List, Optional, Union
from odbms import Model
import logging

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class LLMResponse(Model):
    """Class to handle llm responses (now expects JSON output)"""
    
    llm_response: Optional[str] = None
    """LLM response (raw string)"""
    
    chunks: List[str] = []
    """List of chunks"""
    current_chunk: str = ''
    """Current chunk"""
    
    result: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = []
    artifacts: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None
    observation: Optional[str] = None
    thought: Optional[Union[str, List[str]]] = None
    mindspace: Optional[Union[str, List[str]]] = None
    reflection: Optional[Union[str, List[str]]] = None
    type: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    scratchpad: Optional[str] = None
    todo: Optional[List[str]] = None
    response_overview: Optional[str] = None
    task_summary: Optional[str] = None
    evaluation: Optional[Dict[str, Any]] = None
    overall_assessment: Optional[str] = None
    suggestions: Optional[List[str]] = None
    finalscore: Optional[Union[str, float, int]] = None
    title: Optional[str] = None
    task_title: Optional[str] = None
    task_description: Optional[str] = None
    steps: Optional[List[str]] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[List[str]] = None
    members: Optional[List[str]] = None

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
                    setattr(self, 'tool_calls', value)
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
            
