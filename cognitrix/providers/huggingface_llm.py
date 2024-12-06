import json
from openai import OpenAI
from cognitrix.providers import OpenAI as OpenAILLM
from typing import List, Dict, Any
from openai import OpenAIError
import asyncio
import logging
import os

from cognitrix.providers.base import LLMResponse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')


class Huggingface(OpenAILLM):
    """A class for interacting with the Huggingface API.

    Args:
        model: The name of the Huggingface model to use.
        temperature: The temperature to use when generating text.
        api_key: Your Huggingface ACCESS TOKEN.
    """
    
    model: str = 'Qwen/QwQ-32B-Preview'
    """model endpoint to use""" 
    
    base_url: str = 'https://api-inference.huggingface.co/v1/'
    # base_url: str = 'https://gateway.helicone.ai'
    """Base URL for the Huggingface API"""
    
    temperature: float = 0.2
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('HUGGINGFACE_ACCESS_TOKEN', '')
    """Huggingface ACCESS TOKEN""" 
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    system_prompt: str = ""
    """System prompt to prepend to queries"""
    
    max_tokens: int = 8192
    """Maximum output tokens"""
    
    supports_tool_use: bool = False
    """Whether the provider supports tool use"""
    