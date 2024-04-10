from cognitrix.llms.base import LLM
from cognitrix.utils import image_to_base64
from typing import Any
from dotenv import load_dotenv
from anthropic import Anthropic as AnthropicLLM
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


class Anthropic(LLM):
    """A class for interacting with the Claude API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    model: str = 'claude-3-opus-20240229'
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('ANTHROPIC_API_KEY', '')
    """ANTHROPIC API key""" 
    
    max_tokens: int = 4000
    """The maximum number of tokens to generate in the completion.""" 
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""
    
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
                    "content": [
                        {
                            "type": "text",
                            "text": fm['message']
                        }
                    ]
                })
            else:
                base64_image = image_to_base64(fm['image'])
                messages.append({
                    "role": fm['role'].lower(),
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64_image 
                            }
                        },
                        {
                            "type": "text",
                            "text": "This is the result of the latest screenshot"
                        }
                    ]
                })
            
        return messages

    def __call__(self, query: dict, **kwds: Any)->str|None:
        """Generates a response to a query using the Claude API.

        Args:
            query (dict): The query to generate a response to.
            kwds (dict): Additional keyword arguments to pass to the Claude API.

        Returns:
            str|None: A string containing the generated response.
        """

        client = AnthropicLLM(api_key=self.api_key)
        result = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=self.system_prompt,
            messages=self.format_query(query)
        )
        
        return result.content[0].text