from spiral.llms.base import LLM
from typing import Any
import google.generativeai as genai
from dotenv import load_dotenv
import logging
import sys
import os

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('spiral.log')
load_dotenv()


class Gemini(LLM):
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
    model: str = 'gemini-pro'
    """model endpoint to use""" 
    
    vision_model: str = 'gemini-pro-vision'
    """vision model endpoint to use""" 
    
    temperature: float = 0.0
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('GOOGLE_API_KEY', '')
    """GOOGLE API key""" 
    
    supports_system_prompt: bool = False
    """Flag to indicate if system prompt should be supported"""
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""
    
    def format_query(self, message: dict[str, str]) -> list:
        """Formats a message for the Gemini API"""
        formatted_message = [*self.chat_history, message]
        
        messages = []
        if self.system_prompt:
            messages.append(self.system_prompt)
        
        for fm in formatted_message:
            if fm['type'] == 'text': 
                messages.append(fm['message'])
            elif fm['type'] == 'image':
                self.model = self.vision_model
                messages.append(fm['image'])
                messages.append('Above is the screenshot')

        return messages

    def __call__(self, query, **kwds: Any)->str|None:
        """Generates a response to a query using the Gemini API.

        Args:
        query: The query to generate a response to.
        kwds: Additional keyword arguments to pass to the Gemini API.

        Returns:
        A string containing the generated response.
        """
        genai.configure(api_key=self.api_key)

        
        general_config = {
            "max_output_tokens": 2048,
            "temperature": self.temperature,
            "top_p": 1,
            "top_k": 32
        }
        contents = self.format_query(query)
        
        client = genai.GenerativeModel(self.model)
        
        response = client.generate_content(
            contents,
            stream=True,
            generation_config=general_config    # type: ignore
        )

        response.resolve()
        
        return response.text
    
if __name__ == "__main__":
    try:
        assistant = Gemini()
        # assistant.add_tool(calculator)
        while True:
            message = input("\nEnter Query$ ")
            result = assistant(message)
            print(result)
    except KeyboardInterrupt:
        sys.exit(1)
