import json
from pydantic import BaseModel, Field
from typing import List, Dict
import logging
import inspect

from cognitrix.tools.base import Tool

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

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
    
    tools: List[Tool] = []
    """Functions calling tools"""
    
    def __init__(self, **data):
        super().__init__(**data)
        self.provider = self.__class__.__name__
    
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
            llm: type[LLM] = [f[1] for f in inspect.getmembers(module, inspect.isclass) if f[0].lower() == model_name][0]
            return llm
        except Exception as e:
            logging.exception(e)
            return None
    
    def __call__(*args, **kwargs):
        pass