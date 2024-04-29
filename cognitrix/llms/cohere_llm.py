import cohere
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

class Cohere(LLM):
    """A class for interacting with the Cohere API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    model: str = 'command-nightly'
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    api_key: str = os.getenv('CO_API_KEY', '')
    """Cohere API key""" 

    def __call__(self, query, **kwds: Any)->str:
        """Generates a response to a query using the Cohere API.

        Args:
        query: The query to generate a response to.
        kwds: Additional keyword arguments to pass to the Cohere API.

        Returns:
        A string containing the generated response.
        """
        
        client = cohere.Client(api_key=self.api_key)
        response = client.chat( 
            model=self.model,
            message=query['message'],
            temperature=self.temperature,
            chat_history=self.chat_history,
            preamble_override=self.system_prompt,
            prompt_truncation='auto',
            stream=False,
            citation_quality='accurate',
            connectors=[{"id": "web-search"}]
        )
        
        return response.text
    
if __name__ == "__main__":
    try:
        assistant = Cohere()
        # assistant.add_tool(calculator)
        while True:
            message = input("\nEnter Query$ ")
            result = assistant(message)
            print(result)
    except KeyboardInterrupt:
        sys.exit(1)
