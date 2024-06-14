from clarifai.client.model import Model
from cognitrix.llms.base import LLM, LLMResponse
from typing import Any, Optional
from dotenv import load_dotenv
import logging
import json
import sys
import os
import ast

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()

class Clarifai(LLM):
    """A class for interacting with the Clarifai API.

    Args:
        model: The name of the Clarifai model to use.
        temperature: The temperature to use when generating text.
        api_key: Your Clarifai Personal Access Token.
    """
    model: str = "https://clarifai.com/anthropic/completion/models/claude-3-opus"
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('CLARIFAI_API_KEY', '')
    """Clarifai Personal Access Token""" 

    def __call__(self, query, **kwds: Any):
        """Generates a response to a query using the Clarifai API.

        Args:
        query: The query to generate a response to.
        kwds: Additional keyword arguments to pass to the Clarifai API.

        Returns:
        A string containing the generated response.
        """
        if not self.client:
            self.client = Model(url=self.model)
            
        formatted_messages = self.format_query(query)
            
        query = f"{self.system_prompt}\n {json.dumps(formatted_messages)}"
        result = self.client.predict_by_bytes(query.encode(), input_type="text")
            
        return LLMResponse(result.outputs[0].data.text.raw)
    