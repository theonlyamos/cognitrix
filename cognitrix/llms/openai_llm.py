from openai import OpenAI as OpenAILLM
from cognitrix.llms.base import LLM, LLMResponse
from cognitrix.tools.base import Tool
from cognitrix.utils import image_to_base64
from typing import Any, List, Optional
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


class OpenAI(LLM):
    """A class for interacting with the OpenAI API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    
    model: str = 'gpt-4o'
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    api_key: str = os.getenv('OPENAI_API_KEY', '')
    """OpenAI API key""" 
    
    max_tokens: int = 4096
    """The maximum number of tokens to generate in the completion.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    system_prompt: str = ""
    """System prompt to prepend to queries"""
    
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
            elif fm['type'] == 'image':
                base64_image = image_to_base64(fm['image'])
                messages.append({
                    "role": fm['role'].lower(),
                    "content": [
                        {
                            "type": "text",
                            "text": "This is the result of the latest screenshot"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                })
            else:
                print(fm)
        
        return messages

    def __call__(self, query: dict, **kwds: dict):
        """Generates a response to a query using the OpenAI API.

        Args:
            query (dict): The query to generate a response to.
            kwds (dict): Additional keyword arguments to pass to the OpenAI API.

        Returns:
            A string containing the generated response.
        """

        if not self.client:
            self.client = OpenAILLM(api_key=self.api_key)
        
        if self.base_url:
            self.client.base_url = self.base_url
            
        formatted_messages = self.format_query(query)
        print(self.tools[0]['function']['name'])
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                *formatted_messages
            ],
            temperature=self.temperature,
            tools=self.tools, # type: ignore
            tool_choice='auto',
            max_tokens=self.max_tokens
        )
        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls
        print(tool_calls)
 
        return LLMResponse(response_message.content)
