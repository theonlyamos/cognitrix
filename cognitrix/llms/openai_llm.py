from openai import OpenAI as OpenAILLM
from cognitrix.llms.base import LLM, LLMResponse
from cognitrix.tools.base import Tool
from cognitrix.utils import image_to_base64
from typing import Any, List, Optional
from dotenv import load_dotenv
import logging
import sys
import os

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()


class OpenAI(LLM):
    """A class for interacting with the OpenAI API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    
    model: str = 'gpt-4o-mini-2024-07-18'
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    api_key: str = os.getenv('OPENAI_API_KEY', '')
    """OpenAI API key""" 
    
    max_tokens: int = 4096
    """The maximum number of tokens to generate in the completion.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    system_prompt: str = ""
    """System prompt to prepend to queries"""
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""