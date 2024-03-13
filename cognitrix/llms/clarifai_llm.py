from clarifai.client.model import Model
from cognitrix.llms.base import LLM
from typing import Any, Optional
from dotenv import load_dotenv
import logging
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
    model: str = "https://clarifai.com/mistralai/completion/models/mixtral-8x7B-Instruct-v0_1"
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('CLARIFAI_API_KEY', '')
    """Clarifai Personal Access Token""" 

    def __call__(self, query, **kwds: Any)->str:
        """Generates a response to a query using the Clarifai API.

        Args:
        query: The query to generate a response to.
        kwds: Additional keyword arguments to pass to the Clarifai API.

        Returns:
        A string containing the generated response.
        """

        client = Model(url=self.model)
        query = f"<s> [INST] {query} [/INST]"
        result = client.predict_by_bytes(query.encode(), input_type="text")
            
        return result.outputs[0].data.text.raw
    