from cognitrix.llms.base import LLM, LLMResponse
from typing import Any, Dict, List
import google.generativeai as genai
from google.generativeai import GenerationConfig
from dotenv import load_dotenv
from PIL import Image
import logging
import sys
import os
import io
import asyncio
from google.api_core import exceptions as google_exceptions

from cognitrix.utils import image_to_base64

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()


class Google(LLM):
    """A class for interacting with the Gemini API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    model: str = 'gemini-1.5-pro-exp-0827'
    """model endpoint to use""" 
    
    temperature: float = 0.2
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('GOOGLE_API_KEY', '')
    """GOOGLE API key""" 
    
    max_tokens: int = 8192
    """Maximum output tokens"""
    
    supports_system_prompt: bool = False
    """Flag to indicate if system prompt should be supported"""
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""
    
    def format_query(self, message: dict[str, str], system_prompt: str, chat_history: List[Dict[str, str]]) -> list:
        """Formats messages for the Gemini API"""
        formatted_message = [*chat_history, message]
        
        messages = []
        if system_prompt:
            messages.append(system_prompt)
        
        for fm in formatted_message:
            if fm['type'] == 'text': 
                messages.append(fm['message'])
            elif fm['type'] == 'image':
                screenshot_bytes = io.BytesIO()
                
                fm['image'].save(screenshot_bytes, format='JPEG') # type: ignore
                upload_image = Image.open(screenshot_bytes)
                messages.append(upload_image)
                messages.append('Above is the screenshot')

        return messages

    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        try:
            genai.configure(api_key=self.api_key)

            generation_config = GenerationConfig(
                temperature=self.temperature,
                top_p=0.95,
                top_k=64,
                max_output_tokens=self.max_tokens
            )
            
            contents = self.format_query(query, system_prompt, chat_history)

            if not self.client:
                self.client = genai.GenerativeModel(model_name=self.model, generation_config=generation_config)
            
            response = LLMResponse()
            
            stream =  self.client.generate_content(
                contents,
                stream=True
            )
            
            for chunk in stream:
                response.add_chunk(chunk.text)
                yield response
            
            # response.parse_llm_response()
            # yield response

        except google_exceptions.GoogleAPIError as e:
            logger.error(f"Google API error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: Google API encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Request to Google API timed out")
            yield LLMResponse(llm_response="Error: Request to Google timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in GoogleLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in GoogleLLM: {str(e)}")

