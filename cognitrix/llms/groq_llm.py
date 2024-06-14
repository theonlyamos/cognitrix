from groq import Groq as GroqLLM
from cognitrix.llms.base import LLM, LLMResponse
from typing import Any, Optional
from dotenv import load_dotenv
import logging
import sys
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
    model: str = 'llama3-70b-8192'
    """model endpoint to use""" 
    
    temperature: float = 0.2
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('GROQ_API_KEY', '')
    """Groq API key""" 
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    system_prompt: str = ""
    """System prompt to prepend to queries"""
    
    max_tokens: int = 4096
    """Maximum output tokens"""
    
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
            # if isinstance(fm['message'], list):
            #     for msg in fm['message']:
            #         messages.append(msg)
            
            if fm['type'] == 'text':
                messages.append({
                    "role": fm['role'].lower(),
                    "content": fm['message']
                })
        
        return messages


    def __call__(self, query, **kwds: Any):
        """Generates a response to a query using the Groq API.

        Args:
        query: The query to generate a response to.
        kwds: Additional keyword arguments to pass to the Groq API.

        Returns:
        A string containing the generated response.
        """

        if not self.client:
            self.client = GroqLLM(api_key=self.api_key)
            
        formatted_messages = self.format_query(query)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                *formatted_messages
            ],
            temperature=self.temperature,
            top_p=1,
            stop=None,
            # tools=self.tools,  # type: ignore
            # tool_choice="auto", # type: ignore
            max_tokens=self.max_tokens
        )
        response_message = response.choices[0].message
        # tool_calls = response_message.tool_calls
        # print(tool_calls)
 
        return LLMResponse(response_message.content)
    
if __name__ == "__main__":
    try:
        assistant = Groq()
        # assistant.add_tool(calculator)
        while True:
            message = input("\nEnter Query$ ")
            result = assistant(message)
            print(result)
    except KeyboardInterrupt:
        sys.exit(1)
