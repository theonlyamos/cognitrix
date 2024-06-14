import json
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional, TypedDict
import logging
import inspect

from cognitrix.utils import extract_json, image_to_base64
from cognitrix.tools.base import Tool

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class LLMResponse:
    """Class to handle and separate LLM responses into text and tool calls."""
    
    def __init__(self, llm_response: Optional[str]=None):
        self.llm_response = llm_response
        self.text: Optional[str] = None
        self.tool_calls: Optional[List[Dict[str, Any]]] = None
        self.parse_llm_response()
    
    def parse_llm_response(self):
        """Parse the LLM response into text and tool calls."""
        
        if not self.llm_response: return 
        
        response_data = extract_json(self.llm_response)

        try:
            if isinstance(response_data, dict):
                if 'result' in response_data.keys():
                    self.text = response_data['result']
                else:
                    self.tool_calls = response_data['tool_calls']
            else:
                self.text = str(response_data)
        except Exception as e:
            logger.exception(e)
            self.text = str(response_data)
            

class LLM(BaseModel):
    """
    A class for representing a large language model.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """

    model: str = Field(default=None)
    """model endpoint to use""" 
  
    temperature: float = Field(default=0.4)
    """What sampling temperature to use.""" 

    api_key: str = Field(default=None)
    """API key""" 
    
    base_url: str = ''
    """Base url of local llm server"""
    
    max_tokens: int = Field(default=512)
    """The maximum number of tokens to generate in the completion.""" 
    
    supports_system_prompt: bool = Field(default=False)
    """Whether the model supports system prompts."""
    
    system_prompt: str = Field(default='')
    """System prompt to use for context."""
    
    is_multimodal: bool = Field(default=False)
    """Whether the model is multimodal."""
    
    provider: str = Field(default="")
    """This is set to the name of the class"""
    
    chat_history: List[Dict[str, str]] = []
    """Chat history stored as a list of responses"""
    
    tools: List[Dict] = []
    """Functions calling tools formatted for this specific llm provider"""
    
    supports_tool_use: bool = True
    """Whether the provider supports tool use"""
    
    client: Any = None
    """The client object for the llm provider"""
    
    def __init__(self, **data):
        super().__init__(**data)
        self.provider = self.__class__.__name__
    
    def format_query(self, message: dict[str, str]) -> list:
        """Formats a message for the Claude API.

        Args:
            message (dict[str, str]): The message to be formatted for the Claude API.

        Returns:
            list: A list of formatted messages for the Claude API.
        """
        
        formatted_message = [*self.chat_history, message]
        
        messages = []
        
        for fm in formatted_message:
            if fm['type'] == 'text':
                messages.append({
                    "role": fm['role'].lower(),
                    "content": [
                        {
                            "type": "text",
                            "text": fm['message']
                        }
                    ]
                })
            elif fm['type'] == 'image':
                base64_image = image_to_base64(fm['image']) # type: ignore
                messages.append({
                    "role": fm['role'].lower(),
                    "content": [
                        {
                            "type": "text",
                            "text": "This is the result of the latest screenshot"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                })
            else:
                print(fm)
        
        return messages

    
    def format_tools(self, tools: list[dict[str, Any]]):
        """Format tools for the provider sdk"""
        for tool in tools:
            f_tool = {
                "type": "function",
                "function": {
                    "name": tool['name'].replace(' ', '_'),
                    "description": tool['description'][:1024],
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": tool['required'],
                    },
                },
            }
            for key, value in tool['parameters'].items():
                f_tool['function']['parameters']['properties'][key] = {'type': value}
            
            self.tools.append(f_tool)
    
    @staticmethod
    def list_llms():
        """List all supported LLMs"""
        try:
            module = __import__(str(__package__), fromlist=['__init__'])
            return [f[0] for f in inspect.getmembers(module, inspect.isclass) if f[0] != 'LLM']
        except Exception as e:
            logging.exception(e)
            return []
    
    @staticmethod
    def load_llm(model_name: str):
        """Dynamically load LLMs based on name"""
        try:
            model_name = model_name.lower()
            module = __import__(str(__package__), fromlist=[model_name])
            llm = [f[1] for f in inspect.getmembers(module, inspect.isclass) if (len(f) and f[0].lower() == model_name)]
            return llm[0] if len(llm) else None
        except Exception as e:
            logging.exception(e)
            return None
    
    def __call__(*args, **kwargs):
        return LLMResponse()