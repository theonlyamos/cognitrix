from cognitrix.llms.base import LLM
from dotenv import load_dotenv
import logging
import os

from cognitrix.utils import image_to_base64

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()


class Groq(LLM):
    """A class for interacting with the Groq API.

    Args:
        model: The name of the Groq model to use.
        temperature: The temperature to use when generating text.
        api_key: Your Groq API key.
    """
    model: str = 'llama-3.1-70b-versatile'
    """model endpoint to use""" 
    
    temperature: float = 0.2
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('GROQ_API_KEY', '')
    """Groq API key""" 
    
    base_url: str = 'https://api.groq.com/openai/v1'
    """Base URL for the Groq API"""
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    system_prompt: str = ""
    """System prompt to prepend to queries"""
    
    max_tokens: int = 4096
    """Maximum output tokens"""