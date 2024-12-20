import asyncio
from openai import OpenAI as OpenAILLM
from cognitrix.providers.base import LLM, LLMResponse
from cognitrix.utils import image_to_base64
from typing import Any, Dict, List, Optional
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


class Local(LLM):
    """A class for interacting with the Local llm API.

    Args:
        model (str): The name of the Local model to use
        temperature (float): The temperature to use when generating text
        base_url (str): Base url of local llm server
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    
    model: str = 'local-model'
    """model endpoint to use""" 
    
    vision_model: str = 'local-model'
    """vision model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    api_key: str = 'not-needed'
    """API key""" 
    
    base_url: str = 'http://localhost:1234/v1'
    """Base url of local llm server"""
    
    max_tokens: int = 2048
    """The maximum number of tokens to generate in the completion.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    def format_query(self, message: dict[str, str]) -> list:
        """Formats a message for the Claude API.

        Args:
            message (dict[str, str]): The message to be formatted for the Claude API.

        Returns:
            list: A list of formatted messages for the Claude API.
        """
        
        formatted_message = [*self.chat_history, message]
        
        messages = []
        
        for fm in formatted_message:
            if fm['type'] == 'text':
                messages.append({
                    "role": fm['role'].lower(),
                    "content": fm['content']
                })
            else:
                base64_image = image_to_base64(fm['content'])
                self.model = self.vision_model
                messages.append({
                    "role": fm['role'].lower(),
                    "content": [
                        {
                            "type": "text",
                            "text": "This is the result of the latest screenshot"
                        },
                        {
                            "type": "image_url",
                            "image_url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    ]
                })
            
        return messages

    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        """Generates a response to a query using the OpenAI API.

        Args:
            query (dict): The query to generate a response to.
            kwds (dict): Additional keyword arguments to pass to the OpenAI API.

        Returns:
            A string containing the generated response.
        """
        try:
            if not self.client:
                self.client = OpenAILLM(base_url=self.base_url, api_key=self.api_key)
            
            formatted_messages = self.format_query(query)
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *formatted_messages
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
                
            response = LLMResponse()
            response.add_chunk(response.choices[0].message.content) # type: ignore
            yield response
        
        except RuntimeError as e:
            logger.error(f"Local LLM error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: Local LLM encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Local LLM processing timed out")
            yield LLMResponse(llm_response="Error: Local LLM processing timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in LocalLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in LocalLLM: {str(e)}")#type: ignore
