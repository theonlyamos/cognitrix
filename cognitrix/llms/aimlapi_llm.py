from cognitrix.llms import OpenAI
from typing import Optional
import os

from cognitrix.llms.base import LLMResponse

class AIMLAPI(OpenAI):
    """A class for interacting with the aimlapi API."""
    
    model: str ='gpt-4o'
    
    api_key: str = os.getenv('AIMLAPI_API_KEY', '')
    
    base_url: str = 'https://api.aimlapi.com'
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""
    