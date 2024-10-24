from clarifai.client.model import Model
from cognitrix.providers.base import LLM, LLMResponse
from typing import Any, Dict, List
from dotenv import load_dotenv
import logging
import json
import os
import asyncio

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
    model: str = "https://clarifai.com/anthropic/completion/models/claude-3_5-sonnet"
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('CLARIFAI_ACCESS_TOKEN', '')
    """Clarifai Personal Access Token""" 
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""

    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        """Generates a response to a query using the Clarifai API.

        Args:
        query: The query to generate a response to.
        kwds: Additional keyword arguments to pass to the Clarifai API.

        Returns:
        A string containing the generated response.
        """
        try:
            if not self.client:
                self.client = Model(url=self.model, pat=self.api_key)
                
            formatted_messages = self.format_query(query, chat_history)
            
            message = f"{system_prompt}\n {json.dumps(formatted_messages)}"
            result = self.client.predict_by_bytes(message.encode(), input_type="text")
            response = LLMResponse()
            response.add_chunk(result.outputs[0].data.text.raw)
            yield response
        except asyncio.TimeoutError:
            logger.error("Request to Anthropic API timed out")
            yield LLMResponse(llm_response="Error: Request to Clarifai timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in ClarifaiLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in ClarifaiLLM: {str(e)}")
