from cognitrix.llms import OpenAI
from openai import OpenAI
import os

from cognitrix.llms.base import LLMResponse

class MindsDB(OpenAI):
    """A class for interacting with the MindsDB API."""
    
    model: str ='gemini-1.5-pro'
    
    api_key: str = os.getenv('MINDS_API_KEY', '')
    
    base_url: str = 'https://llm.mdb.ai'
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""