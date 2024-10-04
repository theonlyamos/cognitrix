from typing import Any, Dict, List, Optional, Union
from cognitrix.utils import xml_to_dict
from odbms import Model
import logging

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class LLMResponse(Model):
    """Class to handle llm responses"""
    
    llm_response: Optional[str] = None
    """LLM response"""
    
    chunks: List[str] = []
    """List of chunks"""
    current_chunk: str = ''
    """Current chunk"""
    
    result: Optional[str] = None
    """Result"""
    
    tool_call: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None
    """Tool calls"""
    
    artifacts: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None
    """Artifacts"""
    
    tool_calls: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None
    """Artifacts"""
    
    observation: Optional[str] = None
    """Observation"""
    
    thought: Optional[str] = None
    """Thought"""
    
    mindspace: Optional[str] = None
    """Mindspace"""
    
    reflection: Optional[str] = None
    """Reflection"""
    
    type: Optional[str] = None
    """Type"""
    
    before: Optional[str] = None
    """Before"""
    
    after: Optional[str] = None
    """After"""
    
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
        response_data = xml_to_dict(self.llm_response)

        try:
            if isinstance(response_data, dict):
                response = response_data['response']
                if isinstance(response, dict):
                    for key, value in response.items():
                        setattr(self, key, value)
                else:
                    self.result = response

        except Exception as e:
            logger.exception(e)
            self.result = str(response_data)
            
