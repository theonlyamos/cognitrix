from typing import Any, Dict, Optional
from cognitrix.utils import xml_to_dict
import logging

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class LLMResponse:
    """Class to handle and separate LLM responses into text and tool calls."""
    
    def __init__(self, llm_response: Optional[str]=None):
        self.chunks = []
        self.llm_response = llm_response
        self.current_chunk: str = ''
        self.text: Optional[str] = None
        self.result: Optional[str] = None
        self.tool_calls: Optional[Dict[str, Any]] = None
        self.artifacts: Optional[Dict[str, Any]] = None
        self.observation: Optional[str] = None
        self.thought: Optional[str] = None
        self.type: Optional[str] = None
        self.before: Optional[str] = None
        self.after: Optional[str] = None

        self.parse_llm_response()
    
    def add_chunk(self, chunk):
        self.current_chunk = chunk
        self.chunks.append(chunk)
        self.parse_llm_response()

    def parse_llm_response(self):
        self.llm_response = ''.join(self.chunks)
        response_data = xml_to_dict(self.llm_response)

        try:
            if isinstance(response_data, dict):
                response = response_data['response']
                if isinstance(response, dict):
                    for key, value in response.items():
                        if key == 'result':
                            self.text = value
                        setattr(self, key, value)

                else:
                    self.text = response

        except Exception as e:
            logger.exception(e)
            self.text = str(response_data)
            
